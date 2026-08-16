---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
revised: 2026-08-16
revision_applied: true
revision_applied_at: 2026-08-16T04:05:00Z
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

**A third module carries the identical defect, and it sits in the bridge watchdog's own import
path.** `scripts/log_rotate.py:62-66` calls `logging.basicConfig(level=INFO, format=..., stream=stderr)`
at module scope, immediately before binding `logger = logging.getLogger("log_rotate")` at `:67`. The
module has a `if __name__ == "__main__":` guard at `:190`, so the call has a correct home and is
simply in the wrong place. `monitoring/bridge_watchdog.py:72` reaches it transitively:

```
monitoring/bridge_watchdog.py:72   from scripts.update.service import ...
  → scripts/update/__init__.py → scripts/update/run.py:34
    → scripts/update/log_cleanup.py:17   from scripts import log_rotate
      → scripts/log_rotate.py:62         logging.basicConfig(...)
```

Every hop is module scope. The consequence is that a bare `import monitoring.bridge_watchdog` leaves
root holding an INFO `StreamHandler` that belongs to a log rotator, and every later `basicConfig` in
the process — including the watchdog's own at `:78` — is silently a no-op. Fixing the two watchdogs
while leaving this one behind would be a half-migration, and it would leave acceptance criterion 2
("attaches no handlers to the root logger") unprovable in its plain form. It is fixed here, in the
same PR, with the same transformation.

**Desired outcome:**

Importing any of the three modules has zero logging side effects: no handler on root, no handler
holding a production log file, no directory created. Configuration happens exactly once, at each
script's entry point, where launchd reaches it. `logs/watchdog.log`, `logs/worker_watchdog.log`, and
`logs/log_rotate_error.log` all keep the same path, formatter, level, and rotation settings they have
today.

## Freshness Check

**Baseline commit:** `b44cf02f3` (revision-5 re-measurement, 2026-08-16; previously `0babdb2ab`,
`8877be374`, `1d1830fc4`, originally `3605ff847`).
**Issue filed at:** 2026-08-07T06:19:13Z
**Disposition:** **Minor drift.** The three defect sites are verbatim unchanged and the fix is
unchanged. What has drifted is this plan's *environmental* evidence base — which launchd agents are
installed on this checkout, and what `logs/watchdog.log` actually contains. See
"Revision-5 re-measurement" below; it supersedes every earlier statement in this plan about the
installed agents, the contents of `logs/watchdog.log`, and the No-Go baseline figures. The
"Revision-4 re-verification" subsection that follows it remains authoritative for *source*
coordinates.

### Revision-5 re-measurement (2026-08-16, baseline `b44cf02f3`)

Round 6 recorded two BLOCKERs, both measurement failures rather than design failures. Everything
re-measured here was measured on `/Users/valorengels/src/ai` at `b44cf02f3`.

**Defect sites — still exact, no drift.** Re-read verbatim this round:
`monitoring/bridge_watchdog.py:78` `logging.basicConfig(`, `:82` `logger = logging.getLogger(__name__)`,
`:86` `LOGS_DIR.mkdir(...)`, `:93` `logger.addHandler(_watchdog_file_handler)`;
`monitoring/worker_watchdog.py:162` `logger = _configure_logger()`;
`scripts/log_rotate.py:62` `logging.basicConfig(` and `:67` `logger = logging.getLogger("log_rotate")`.
Entry-point guards: `bridge_watchdog.py:1084`, `worker_watchdog.py:873`, `log_rotate.py:190`.
Symbols: `main()` `:1011`, `run_health_check()` `:903`, `check_bridge_health()` `:557`,
`kill_zombie_processes()` `:512`. The root cause is untouched and the Solution stands as written.

**The launchd evidence base was inverted, and is now measured.** Earlier revisions asserted that
`com.valor.bridge-watchdog` is not installed here and that `com.valor.worker-watchdog` is. Both
statements are false on this checkout. Measured:

| Agent | `launchctl list` | `~/Library/LaunchAgents/` | Plist redirect |
|---|---|---|---|
| `com.valor.bridge-watchdog` | **present** (`-  0  com.valor.bridge-watchdog`) | **present** — `com.valor.bridge-watchdog.plist` | `StartInterval 60`; `StandardOutPath` **and** `StandardErrorPath` both `/Users/valorengels/src/ai/logs/watchdog.log` |
| `com.valor.worker-watchdog` | **absent** | **absent** | n/a — not installed |

The bridge watchdog runs here every 60 s against this very checkout, invoking
`.venv/bin/python monitoring/bridge_watchdog.py` with `WorkingDirectory` `/Users/valorengels/src/ai`.
Two consequences run through the rest of this plan: the doubling claim gains a **direct on-machine
proof** (below), and the "1 copy" control experiment built on `com.valor.worker-watchdog` is
**withdrawn** — there is no live worker watchdog here to control against.

**`logs/watchdog.log`, measured rather than characterised.** 9,694,277 bytes, 102,627 lines,
spanning `2026-07-22 08:51:33` to the present tick. `maxBytes` is 10,485,760, so headroom is
~791 KB. Composition, computed by parsing every line into the watchdog's own bracketed format
(`%(asctime)s [%(levelname)s] %(message)s`) versus `log_rotate`'s bare root format
(`%(asctime)s %(levelname)s %(message)s`) and matching on the exact `(timestamp, level, message)`
triple:

| Class | Lines | What it is |
|---|---|---|
| Bracketed with an exact bare twin | **38,706** | `monitoring.bridge_watchdog`'s own records, arriving **twice** — once via the `RotatingFileHandler`, once via root's `StreamHandler` → stderr → plist. This is Data Flow row 1, measured directly on production output |
| Bare with no bracketed twin | **24,144** | submodule records riding root's handler. **22,230 (92.1%) are the single record `INFO libssl detected, it will be used for encryption`**, one per 60 s tick; the remaining 1,914 are WARNING+ |
| Bracketed with no bare twin | **1,063** | test pollution — the defect this issue is about (442 at 1x, 207 distinct messages at exactly 3x, the reload-stacking signature) |
| Unparsed | 8 | one `ModuleNotFoundError` traceback, interpreter → stderr → plist |

Exemplar of the doubling, verbatim at lines 225-226 of the current file:

```
2026-08-16 03:07:38,488 [INFO] Recovery successful
2026-08-16 03:07:38,488 INFO Recovery successful
```

**This replaces the "honest limit" disclaimer with evidence.** The earlier text said the doubling
"is derived from the plist text and the logging semantics, not from this file's contents". It is now
derived from both: 38,706 measured duplicate pairs in production output on this machine.

**Rotation is routine, and the gate must survive it.** Backups `.1` through `.5` exist at
~10,486,xxx bytes each, dated Jul 22 / Jul 18 / Jul 14 / Jul 10 / Jul 7. The file has rolled over
five times inside the retained window. Growth, measured as bytes per calendar day for Aug 9-15:
178,678 / 247,355 / 114,654 / 113,665 / 120,925 / 122,959 / 113,665 — mean **144,557 B/day**, so the
~791 KB of headroom is roughly **5.5 days**. (Round 6's "~1.07 MB/day, rollover within ~18 hours"
derived megabytes from line counts and overstates the rate by ~7x; the hazard is real, the deadline
is not that tight.) Either way a rollover during a multi-day build is entirely plausible and has
happened five times recently, so the No-Go prefix gate **must** treat a rollover as legitimate and
check `$f.1` before declaring `TRUNCATED`. That fallback is specified in Task 1.

**No literal log baseline appears anywhere in this plan any more.** At ~145 KB/day and a live
60-second writer, any byte count or hash written into this document is stale before a builder reads
it. The revision-4 figures (9692506 B / `51cfe6c4…`) and the revision-3 figures (41322 B /
`2c3c2f2d…`) are both superseded and **must not be used**. Task 1 re-measures at build start; that
is the only contract.

**`logs/worker_watchdog.log` is not a production control.** 1,017,993 bytes, 8,708 lines, mtime
`Aug 16 03:02` — the nightly-test burst, not a launchd tick, because no worker-watchdog agent is
installed. The earlier "13,861 lines, multiplicity histogram entirely 1" control experiment is
withdrawn wherever it appears. It also has **no live writer**, which simplifies rather than
complicates the No-Go gate: the prefix-scoped form is retained anyway, because the bridge watchdog
appends to `logs/watchdog.log` every 60 s and any concurrent agent's pytest run can still append to
either file until this PR merges.

### Revision-4 re-verification (2026-08-16, baseline `0babdb2ab`)

**Defect sites — still exact, no drift.** `monitoring/bridge_watchdog.py:78` `basicConfig`, `:86`
`mkdir`, `:93` `addHandler`; `monitoring/worker_watchdog.py:162` `logger = _configure_logger()`;
`scripts/log_rotate.py:62` `basicConfig`. All five re-read verbatim at the cited lines. The root
cause is untouched and the Solution stands as written.

**Two commits landed on main since the revision-3 baseline, both touching referenced files:**

- **`fd52fc648`** — "Wedge detector: require evidence something was missed, and reset on restart
  (#2475) (#2670)". Rewrote 213 lines of `monitoring/bridge_watchdog.py` and **rewrote
  `tests/integration/test_update_loop_wedge_recovery.py` wholesale** (486 lines, the entire file).
  It did not touch the import-time logging block.
- **`fb00b8542`** — "Enforce test-DB ownership so the unit suite stops rotating (#2628) (#2683)".
  Added 19 lines to `tests/unit/test_worker_watchdog.py`: an autouse
  `_restore_worker_watchdog_constants` fixture that snapshots and restores
  `_WWD_MODULE_SCALARS = ("HEARTBEAT_THRESHOLD",)` around every test in the file, because
  `importlib.reload(wwd)` was leaving the constant mutated for later tests. Every line number in
  that file below the insertion point shifted by **+19**.

**Corrected coordinates (round-3 citations that have drifted):**

| Round-3 citation | Correct at `0babdb2ab` |
|---|---|
| `monitoring/bridge_watchdog.py:968` — `def main()` | **`:1011`** |
| `monitoring/bridge_watchdog.py:1041-1042` — `__main__` guard | **`:1084-1085`** |
| `monitoring/bridge_watchdog.py:860` — `def run_health_check()` | **`:903`** |
| `monitoring/bridge_watchdog.py:514` — `def check_bridge_health()` | **`:557`** |
| `monitoring/bridge_watchdog.py` — `def kill_zombie_processes()` | **`:512`** (the INFO lines round 3 cited at `:482`/`:494`/`:500` move with it; re-derive from `:512`+) |
| `monitoring/bridge_watchdog.py:869-871` — hibernation `logger.info` | **`:913`** (`:869-871` is now docstring prose) |
| `bridge/hibernation.py:99` — hibernation `logger.error` | **`:100`** |
| `tests/integration/test_update_loop_wedge_recovery.py:26` — import site | **`:37`** |
| `tests/unit/test_worker_watchdog.py` reloads `47, 52, 58, 1050, 1058` | **`66, 71, 77, 1069, 1077`** |
| `tests/unit/test_worker_watchdog.py` ten `addHandler(caplog.handler)` sites `275, 372, 402, 712, 732, 749, 773, 802, 826, 886` | **`294, 391, 421, 731, 751, 768, 792, 821, 845, 905`** |
| `tests/unit/test_worker_watchdog.py::test_heartbeat_threshold_env_override` (:1050) / `::test_heartbeat_threshold_default_is_180` (:1058) | **`:1063` / `:1072`** |

**No drift** in `tests/unit/test_bridge_watchdog.py`: its two `caplog.at_level` sites are still at
`:753` and `:957`, its five `main()` calls still at `544, 571, 598, 781, 805`, and its two
`importlib.reload(bw)` sites still at `1106, 1114`. That file was not touched by either commit.

**The one substantive change: `tests/integration/test_update_loop_wedge_recovery.py` no longer
contains the tests this plan named.** #2670 replaced the file. The round-3 Test Impact entry cites
caplog at `:156` and `:221` plus an inert third mention at `:60`; none of those exist now. The file's
current caplog surface is **two tests, both new**, and both read `caplog.records` after
`caplog.at_level(logging.WARNING, logger="monitoring.bridge_watchdog")`:

- `test_redis_exception_is_inconclusive(caplog)` — `at_level` at `:371`, `caplog.records` at `:376`
- `test_unreadable_process_start_suppresses_verdict(caplog)` — `at_level` at `:387`,
  `caplog.records` at `:395`

There is no longer an inert third mention. **The remedy is unchanged** — both sites need the same
explicit `bw.logger.addHandler(caplog.handler)` / `removeHandler` `try/finally` conversion, for the
same reason (they read records off a logger this plan sets `propagate = False`). Only the names and
line numbers moved, so the Test Impact entry is re-pointed rather than re-designed.

**Bug still reproducible — and materially worse.** `logs/watchdog.log` is now **9,692,506 bytes**
(mtime `Aug 16 10:48`), up from the 41,322 bytes measured at revision 3, i.e. it has grown ~235x in
nine days and is approaching the 10 MB `maxBytes` rotation threshold — at which point the rotation
would discard genuine history in favor of retained test output. **The No-Go gate baseline is
re-measured to `sha256 = 51cfe6c4d464235c9bc85d9e55449e2487cbdef2c0eb1447d60694e2038f2f9b`,
9692506 bytes, mtime `Aug 16 10:48`.** The round-3 baseline
(`2c3c2f2d467de6d3d00f59c39469760548dd96de221b3e07808fca7792df89de`, 41322 bytes) is superseded and
must not be used — a gate comparing against it would fail for a reason unrelated to the change.

**Lazy-import rejection — re-priced, conclusion unchanged.** Round 3 rejected the lazy import at
`monitoring/bridge_watchdog.py:72` on a cost of "15 sites, including 10
`patch("monitoring.bridge_watchdog.get_process_start_ts")` calls". Measured at `0babdb2ab`, the
three symbols appear on **6** lines across `tests/` in this module's namespace: **1** patch
(`tests/integration/test_update_loop_wedge_recovery.py:75`) and **5** constant reads
(`tests/unit/test_bridge_watchdog.py:872, 886, 887, 889, 914`). The rejection **still holds** — the
five constant reads alone break under a lazy import, since a module-scope name would no longer
exist to read — but it now rests on the measured figure rather than the inflated one. State it that
way; do not re-quote "15 sites".

**Note on the round-5 critique's own freshness.** The round-5 findings table (see Critique Results)
recorded this drift as a BLOCKER and is the basis for most of this section. That critique was
committed `2026-08-10 12:02 +0700`, which is **after** `fd52fc648` (11:37 same day) but **before**
`fb00b8542` (2026-08-13). Its "**Re-verified UNCHANGED**" list therefore still cites
`tests/unit/test_worker_watchdog.py` reloads at `47/52/58/1050/1058` and the ten
`addHandler(caplog.handler)` sites at their pre-`fb00b8542` addresses. Those are now stale by +19;
the table above supersedes them. Everything else in that critique's unchanged list was re-checked
here and still holds.

**Sibling issue re-check:** **#2678** is still **OPEN**, retitled to "scripts/update/run.py has no
entry-point logging configuration and relied on log_rotate's import side effect" — matching the
narrowed scope revision 3 assigned it. No change to this plan's relationship with it.

**Active plans overlapping this area:** still none.

### Round-3 file:line re-verification (superseded where the table above disagrees)

**File:line references re-verified:**

- `monitoring/bridge_watchdog.py:78-81` — `logging.basicConfig(...)` at module scope — still holds,
  exact lines. A fresh interpreter reports `logging.getLogger().handlers == []` and `level == 30`
  **before** the import and `[<StreamHandler <stderr> (NOTSET)>]` / `20` after it. Both the cause and
  the fix are now in scope — see "Two modules configure root, and this plan deletes both" below.
- `monitoring/bridge_watchdog.py:86-93` — `LOGS_DIR.mkdir()` + `RotatingFileHandler` + `addHandler` —
  still holds. A scope-aware AST scan (TC5's exact predicate) over the **14** guarded files — all 13
  `monitoring/*.py` plus `scripts/log_rotate.py` — reports module-scope hits at exactly **five**
  places and nowhere else, re-run at revision 3:

  ```
  monitoring/bridge_watchdog.py:78 basicConfig
  monitoring/bridge_watchdog.py:86 mkdir
  monitoring/bridge_watchdog.py:93 addHandler
  monitoring/worker_watchdog.py:162 _configure_logger
  scripts/log_rotate.py:62 basicConfig
  total hits: 5
  ```

  The fix deletes or relocates all five, so TC5 goes green the moment it lands.
- `scripts/log_rotate.py:62-66` — `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s
  %(message)s", stream=sys.stderr)` at module scope, followed by `logger = logging.getLogger("log_rotate")`
  at `:67` — verified verbatim. `if __name__ == "__main__": sys.exit(main())` at `:190-191`.
  Measured: a bare `import scripts.log_rotate` moves root from `[] / 30` to
  `[<StreamHandler <stderr> (NOTSET)>] / 20`, and `log_rotate.logger.level` is `0` (NOTSET).
- `scripts/update/log_cleanup.py:17` — `from scripts import log_rotate` — verified. It is the **only**
  non-test importer of `log_rotate` in the repo; a repo-wide `--include="*.py"` grep outside
  `.worktrees/` and `.venv` finds no other.
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
- **#2678** — opened during revision 2 to track `scripts/log_rotate.py:62` as out-of-scope follow-up
  work. Revision 3 brings that module **into** this PR, so #2678's body is now largely what this PR
  does. It is narrowed to the one thing that genuinely remains outside these three modules — see
  "What #2678 becomes" below.

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
  *Round-1 read this as a mandate for the watchdogs to configure root. That read is withdrawn for the
  watchdogs and upheld for `log_rotate`.* The sentence describes a process whose stderr is the log
  destination — which is exactly `log_rotate` under its LaunchAgent, and exactly **not** the
  watchdogs, whose stdout and stderr are already redirected into the very file their handler writes.
  Same stdlib idiom, opposite topologies, because the plists differ. See Data Flow.
- `logging.lastResort` is a `_StderrHandler` at level **WARNING** (`<_StderrHandler <stderr> (WARNING)>`,
  `level == 30`), active only when a record reaches a logger with no handlers anywhere in its
  ancestry. Verified empirically in a fresh interpreter with an empty root:
  `logging.getLogger("monitoring.crash_tracker").warning("SUBMODULE WARNING PROBE")` prints the bare
  message — no timestamp, no level tag — to stderr; `.info(...)` prints nothing at all.
  *Informs the plan, and revision 3 restores it to the bridge:* with `scripts/log_rotate.py` also
  fixed, a bare `import monitoring.bridge_watchdog` leaves root at `[] / 30` (measured), so
  `lastResort` **does** fire for submodule WARNING+ in the bridge watchdog's process, and submodule
  INFO/DEBUG is dropped at the call site. Revision 2 recorded this as false-for-bridge because root
  retained log_rotate's handler; that scoping is superseded, not the mechanism. See Data Flow.
- Libraries that must stay silent when unconfigured attach a `NullHandler`. *Not needed here:* all
  three modules are entry points that happen to be importable, and `lastResort` is the documented
  default for the importable half.

## Data Flow

### The doubling, stated precisely

`scripts/valor-service.sh:627-630` points **both** `StandardOutPath` and `StandardErrorPath` of the
`com.valor.bridge-watchdog` plist at `${LOG_DIR}/watchdog.log`. Combined with today's module-scope
configuration, a single launchd tick produces:

Root in this process carries `scripts/log_rotate.py:62`'s `StreamHandler` at level INFO **today**. This
plan fixes `log_rotate` too, so after the change root is genuinely empty at WARNING — measured, not
assumed. Both columns below are derived from measurement.

| Record source | Path to `logs/watchdog.log` today | Copies today | After this change |
|---|---|---|---|
| `monitoring.bridge_watchdog` own records | RotatingFileHandler **and** (via `propagate = True`) root's StreamHandler → stderr → plist | **2** | **1** — RotatingFileHandler only, formatter unchanged |
| Submodule records at WARNING+ (`monitoring.crash_tracker`, `scripts.update.service`, …) | root's StreamHandler → stderr → plist | 1, formatted `%(asctime)s %(levelname)s %(message)s` (log_rotate's format) | **1, via `logging.lastResort` → stderr → plist, UNFORMATTED** (bare message, no timestamp, no level tag) |
| Submodule records at INFO | root's StreamHandler (root is at INFO) → stderr → plist | 1 | **0 — dropped at the call site.** `lastResort` is at WARNING and root is empty |
| Submodule records at DEBUG | dropped by root's INFO level | 0 | 0 (**unchanged**) |
| Uncaught interpreter tracebacks | interpreter → stderr → plist | 1 | 1 (**unchanged**) |

**Three rows change, and two of them are losses.** The watchdog's own records halve (the point of the
change). Submodule WARNING+ records survive but lose their timestamp and level prefix. Submodule INFO
records stop reaching the file entirely. Rows 2 and 3 are the price of the root cleanup, and they are
stated here, in the PR body, and in `docs/features/watchdog-log-isolation.md` rather than discovered
by an operator.

**Row 3 is 92% one noise record, and dropping it is a retention win.** Measured on the current file:
22,230 of the 24,144 submodule-INFO-class lines are `INFO libssl detected, it will be used for
encryption`, emitted once per 60-second tick by a transitively imported crypto library. That single
record is the dominant term in the ~145 KB/day growth that rolls genuine incident history out of the
5-backup budget — the harm the Problem section argues. Row 3 is therefore a loss in form and a gain
in substance, and the PR body must say so, or an operator will read a 90%+ volume drop as a stopped
watchdog. Row 2's 1,914 WARNING+ records are the ones that genuinely lose something (their
timestamp and level prefix), and they survive.

**Measured root state, three points, pinned venv, main checkout:**

| Point | `root.handlers` | `root.level` |
|---|---|---|
| fresh interpreter, before any import | `[]` | 30 (WARNING) |
| after `import monitoring.bridge_watchdog`, **today** | `[<StreamHandler <stderr> (NOTSET)>]` (`_fmt = %(asctime)s %(levelname)s %(message)s`) | 20 (INFO) |
| after `import monitoring.bridge_watchdog`, **with all three modules fixed** | `[]` | 30 (WARNING) |

The third row was measured by neutralizing exactly the statements this plan deletes — the two
module-scope `basicConfig` calls and the module-scope `addHandler` — and re-importing. It is the
literal post-fix state, not a projection.

**There is no fourth `basicConfig` in the graph, and this was checked rather than assumed.** A spy on
`logging.basicConfig`, `Logger.addHandler`, and `Logger.setLevel` (the latter two filtered to the root
logger) recorded every call during a bare `import monitoring.bridge_watchdog`. The complete result:

```
basicConfig: 2 call(s)
    <repo>/scripts/log_rotate.py:62
    <repo>/monitoring/bridge_watchdog.py:78
root_addHandler: 1 call(s)   -> .../logging/__init__.py:2117   (basicConfig's own internals)
root_setLevel:  1 call(s)    -> .../logging/__init__.py:2120   (basicConfig's own internals)
```

Two callers, both fixed by this PR. Nothing else in the graph touches root.

**`logging.lastResort` fires on the bridge again after this change.** Revision 2 recorded it as
false-for-bridge, correctly, given a root that log_rotate had already configured. With log_rotate
fixed the premise is gone: root is empty, so submodule WARNING+ reaches `lastResort` and prints
unformatted. Verified end to end — `SUBMODULE WARNING PROBE` appeared on stderr with no prefix, and
the matching `.info(...)` produced no output at all.

### `scripts/log_rotate.py` — a library-plus-script, and the opposite topology

`log_rotate` is not a watchdog. Its LaunchAgent routes `StandardErrorPath` to
`logs/log_rotate_error.log` and `StandardOutPath` to `logs/log_rotate.log` — **two different files**,
not one file twice. There is no doubling to remove, so there is nothing for `propagate = False` to
buy, and it configures **root** (via `basicConfig`) rather than its own logger.

| Mode | Today | After this change |
|---|---|---|
| Script under LaunchAgent (`python scripts/log_rotate.py`) | `basicConfig` at import → root StreamHandler at INFO → stderr → `logs/log_rotate_error.log`, formatted | **identical.** `basicConfig` moved into the `__main__` guard with the same `level`, `format`, and `stream=sys.stderr` |
| Library, imported by `scripts/update/log_cleanup.py:17` during `/update` | import silently configures the caller's root at INFO; log_rotate's own INFO/WARNING lines print formatted to `/update`'s stderr | root is left alone. `logger.info(...)` is dropped, `logger.warning(...)` reaches `lastResort` unformatted, unless the caller configures logging |
| Any other importer (e.g. `monitoring/bridge_watchdog.py:72`, transitively) | inherits an INFO root handler it never asked for | **no side effect at all** |

**Rehearsed against the real source text, not a description of it.** A byte-for-byte copy of
`scripts/log_rotate.py` with only the two edits applied was run both ways:

- **Script mode** produced `2026-08-08 01:32:20,623 INFO logs dir not found: …` and
  `2026-08-08 01:32:20,623 INFO done: rotated=0 skipped=0` on stderr — the same format, level, and
  stream as today, exit 0. A `runpy.run_path(..., run_name="__main__")` run against the same copy
  left root holding exactly one `StreamHandler` whose `stream is sys.stderr`, `_fmt ==
  "%(asctime)s %(levelname)s %(message)s"`, and `root.level == 20`.
- **Library mode** left root at `[] / 30` after the import, with `log_rotate.logger` at level 20 and
  `propagate True`. `rotate_logs()` on a missing directory printed nothing (its INFO line was
  dropped); an explicit `logger.warning(...)` printed `LIBRARY MODE WARNING PROBE` bare via
  `lastResort`.

**The `/update` consequence, stated plainly.** `scripts/update/run.py` never configures logging — it
has three `logging.getLogger(__name__).warning(...)` calls and five `print()` calls, and everything
operator-facing goes through `print()`. Today those three warnings are formatted only because
`log_rotate`'s import happened to configure root. After this change they reach `lastResort` and print
bare. The same import also stops leaking unrelated libraries' INFO chatter onto `/update`'s stderr
(#2678 recorded one live instance: `INFO Loaded emoji embeddings from cache (72 entries)` from a
probe that only imported `monitoring.bridge_watchdog`). `log_rotate`'s library-mode sweep diagnostics
at `:162`/`:166`/`:171` lose their INFO lines, but `sweep_oversized_logs()` returns a structured
`LogCleanupResult` — removed paths, freed bytes, warnings — and that, not the log records, is what
`/update` reports. **Giving `scripts/update/run.py` its own entry-point configuration is the one
piece of this that stays out of scope**, and it is what #2678 is narrowed to.

**The worker's table is different, and nothing in it changes.** `monitoring/worker_watchdog.py`'s
import graph leaves root genuinely empty, and its logger has been `propagate = False` since #1311. Own
records: 1 copy today, 1 after. Submodule WARNING+: `lastResort` → stderr → plist, unformatted, today
and after. Submodule INFO/DEBUG: dropped today and after. The worker change is purely the removal of
an import-time side effect, with **zero** operator-visible difference in `logs/worker_watchdog.log`.

**Row 1 is proved directly on this machine's production output.** `com.valor.bridge-watchdog` **is**
installed and running here every 60 s (`launchctl list`; `~/Library/LaunchAgents/com.valor.bridge-watchdog.plist`
with `StandardOutPath` and `StandardErrorPath` both `/Users/valorengels/src/ai/logs/watchdog.log`).
Parsing all 102,627 lines of the current `logs/watchdog.log` and matching bracketed lines against
bare lines on the exact `(timestamp, level, message)` triple yields **38,706 own-records present in
both formats** — the `RotatingFileHandler` copy and the root-`StreamHandler`-via-plist copy, side by
side. Exemplar at lines 225-226:

```
2026-08-16 03:07:38,488 [INFO] Recovery successful
2026-08-16 03:07:38,488 INFO Recovery successful
```

Row 3 is measured on the same corpus: of the 24,144 bare lines with no bracketed twin,
**22,230 (92.1%) are the single record `INFO libssl detected, it will be used for encryption`**, one
per launchd tick, and the other 1,914 are WARNING+ (row 2, which survives unformatted). See
Risk 1 — the INFO loss is overwhelmingly this one record.

**The earlier "1 copy" control experiment is withdrawn.** Revisions 2-4 cited
`com.valor.worker-watchdog` as a live production control with a clean multiplicity histogram.
Measured at revision 5: that agent is in **neither** `launchctl list` nor `~/Library/LaunchAgents/`
on this checkout, and `logs/worker_watchdog.log` (8,708 lines, mtime `Aug 16 03:02`) is nightly-test
output, not launchd output. The claim it supported no longer needs a proxy — the bridge watchdog is
live here and row 1 is proved on its own output.

**Honest limit, re-pointed rather than deleted.** The **"after this change" column remains a
projection.** It is derived from the plist text, the measured post-fix root state (`[] / 30`), and
`lastResort`'s documented semantics — not from observing a fixed watchdog under launchd, which
cannot happen before the PR merges. What has changed is that the *today* column is no longer a
projection: every "copies today" figure above is now a count taken from production output on the
machine this build runs on.

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

**Root cause pattern:** all three modules are launchd *scripts* that are also *importable modules*, and
each one performs application-level logging configuration in the importable half. The repeated
failure is treating the symptom visible in one log file rather than the shared structural mistake:
configuration below the entry point.

## Architectural Impact

- **New dependencies**: none. Stdlib `logging` only.
- **Interface changes**: `monitoring/bridge_watchdog.py` gains a private `_configure_logging()` and a
  module-level `WATCHDOG_LOG_FILE` constant. `monitoring/worker_watchdog.py`'s `_configure_logger()`
  changes return type to `None` and its handler clearing to owned-only; its call site moves.
  `scripts/log_rotate.py` keeps its entire public surface (`rotate_logs`, `sweep_oversized_backups`,
  `main`, every constant) and changes only where `basicConfig` is called and the logger's explicit
  level. Nothing public changes anywhere; the only non-test importer of any of the three is
  `scripts/update/log_cleanup.py:17` → `log_rotate`.
- **Behavior change (intentional, operator-visible)**: three effects, all in `logs/watchdog.log` plus
  one on `/update`'s stderr. The watchdog's own records stop being duplicated; submodule WARNING+
  records lose their timestamp/level prefix; submodule INFO records stop arriving.
  `logs/worker_watchdog.log` and `logs/log_rotate_error.log` do not change at all. See Data Flow and
  Risk 1. This is the section Task 7 lifts verbatim into the PR body.
- **Coupling**: decreases in the direction that matters most. Importing *any* of the three modules
  stops mutating process-global logging state — and `scripts/log_rotate.py` was the one whose
  contamination reached furthest, since `scripts/update/*` is imported by unrelated subsystems.
- **Data ownership**: unchanged. Same paths, same rotation budgets, same formatters.
- **Reversibility**: trivial. ~60 lines across three modules plus test edits and one new test file;
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
| Pinned interpreter available | `.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); v='.'.join(map(str,sys.version_info[:2])); assert pin.startswith(v), (pin, v); print('PIN_OK')"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

**The interpreter check must invoke `.venv/bin/python`, not bare `python`** (corrected at revision
4). `python` on PATH is whatever the ambient shell provides — measured as 3.12.13 on this machine
while `.venv/bin/python` is 3.14.3 and `.python-version` pins `3.14` — so the bare form reports
`AssertionError: ('3.14', '3.12')` and fails `scripts/check_prerequisites.py` on a **correctly
provisioned** checkout. Under `.venv/bin/python` the same check prints `PIN_OK`.

Two setup steps are part of Task 1 rather than prerequisites, because the build creates the thing
they check:

- Provision `.worktrees/watchdog-import-time-log-handler/.venv` on the pinned interpreter. A missing
  worktree venv surfaces as a bogus pre-commit "lint" block with no findings listed.
- `export PYTHONPATH=$PWD` in every shell inside the worktree. The shared venv's `.pth` otherwise
  imports the main checkout, which would run the red demo against the production log.

## Solution

### Two modules configure root, and this plan deletes both

`monitoring/bridge_watchdog.py:72` imports `scripts.update.service` for three shared symbols. That
import runs an entirely module-scope chain ending in a `basicConfig` call belonging to a different
subsystem (chain traced in full under Problem). Measured today, in a fresh interpreter:

| Point | `root.handlers` | `root.level` | root handler's `_fmt` |
|---|---|---|---|
| before any import | `[]` | 30 (WARNING) | — |
| after `import scripts.log_rotate` | `[<StreamHandler <stderr>>]` | 20 (INFO) | `%(asctime)s %(levelname)s %(message)s` |
| after `import monitoring.bridge_watchdog` | `[<StreamHandler <stderr>>]` | 20 (INFO) | `%(asctime)s %(levelname)s %(message)s` — **log_rotate's format, not the watchdog's** |

The consequence that shaped revision 2: `bridge_watchdog.py:78`'s `basicConfig` is a **no-op today**,
because `logging.basicConfig()` returns early once root has a handler. Deleting it alone changes
nothing about root, so a `root.handlers == []` gate would fail against correctly fixed watchdogs.
Revision 2 responded by weakening every bridge-side root gate to a delta measurement and filing the
root cause as #2678.

**Revision 3 reverses that.** The defect is the same defect, in a module that sits directly in the
import path of the module being fixed, with a `__main__` guard already present at
`scripts/log_rotate.py:190` waiting to receive the call. Leaving it is the half-migration
Development Principle 1 forbids, and it is what forced acceptance criterion 2 to be restated
attributively instead of proved. The same transformation applies:

```python
# module scope — inert, no I/O, no root mutation
logger = logging.getLogger("log_rotate")
logger.setLevel(logging.INFO)

# ... module body unchanged ...

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
```

`level`, `format`, and `stream=sys.stderr` are preserved exactly, so the module docstring's statement
that the LaunchAgent routes stderr to `logs/log_rotate_error.log` (self-excluded from rotation via
`SELF_EXCLUDED_FILES`) stays accurate. `setLevel(logging.INFO)` is **added**: today the logger is
NOTSET and inherits root's level, which only happens to be INFO because of the very `basicConfig`
being moved. Without the explicit level, library-mode INFO records would start depending on the
caller's root level.

**With all three fixed, acceptance criterion 2 is literally satisfiable, and it is proved literally.**
Measured post-fix state after a bare `import monitoring.bridge_watchdog`:

```
root.handlers = []
root.level    = 30 (WARNING)
```

So TC1 returns to the strong absolute form on the **bridge** side, matching TC3 on the worker side.
The delta-measured TC1b is deleted — `root.handlers == []` strictly dominates it, since any root
handler at all now fails, including one carrying the watchdog's own formatter. TC1a survives in
rescoped form because it names *which* module relapsed rather than only that one did.

**No lazy import at `bridge_watchdog.py:72`. That decision from revision 2 stands unchanged.** The
alternative was to move the import inside `assess_update_flow()` so the contamination never reached
the watchdog's process. It was rejected on measured cost, not taste: those three names are module
attributes the test suite binds to at **15 sites** —
`tests/integration/test_update_loop_wedge_recovery.py` patches
`monitoring.bridge_watchdog.get_process_start_ts` at **10** sites, and 5 further sites read
`bw.UPDATE_REPORT_TTL_SECONDS` / `bw.UPDATE_RESTART_MARKER_TTL_SECONDS`
(`tests/unit/test_bridge_watchdog.py:872, 886, 887, 889, 914`). A lazy import deletes those names
from the module namespace, so every `patch()` target raises `AttributeError` and every constant read
breaks. Restoring them needs wrapper shims or a PEP-562 module `__getattr__` — a namespace redesign
smuggled inside a logging fix, and a direct hit on acceptance criterion 4. Fixing `log_rotate` at
source achieves the same clean root with none of that cost.

### `log_rotate` keeps `propagate = True`, and the watchdogs do not

The two decisions look contradictory and are not. They follow from **which logger each module
configures**, which in turn follows from each plist.

| | `monitoring/*_watchdog.py` | `scripts/log_rotate.py` |
|---|---|---|
| Plist streams | `StandardOutPath` **and** `StandardErrorPath` → the *same* file | `StandardOutPath` → `logs/log_rotate.log`, `StandardErrorPath` → `logs/log_rotate_error.log` — *different* files |
| What the entry point configures | the **module's own** logger (a `RotatingFileHandler`) | **root** (`basicConfig` → stderr) |
| Effect of propagation | a second write path into the same file → every record doubled | the **only** path from the module's records to its handler |
| Decision | `propagate = False` | `propagate = True` (the default, left in place) |

**Measured, because the failure mode is silent.** Adding `logger.propagate = False` to the fixed
`log_rotate` and running it in script mode produced **no output whatsoever** — exit 0, empty stderr,
both INFO lines gone. `logs/log_rotate_error.log` would simply stop being written, and nothing would
report it. The records reach a logger with no handlers, propagation is off, so `lastResort` is the
only remaining path and it is at WARNING, above every line `main()` emits on a healthy run.

`propagate = True` is also the correct library contract: imported by `scripts/update/log_cleanup.py`,
`log_rotate`'s named logger should route to whatever the caller configured, which is the whole point
of moving configuration to the entry point.

**The default is left implicit, and a guard pins it.** No `logger.propagate = True` line is added —
writing a value that is already the default is noise. Instead TC14 asserts `propagate is True` after a
bare import, so a future editor who reads "the watchdogs use `propagate = False`" and cargo-cults it
onto `log_rotate` fails the suite instead of silently emptying a production log file.

### What #2678 becomes

#2678 ("`scripts/log_rotate.py` configures the root logger at import, contaminating every importer")
is what this PR now does. Leaving it open would describe shipped work as pending. Its body already
names the one thing that stays outside these three modules — its closing line: "Verify the same for
`scripts/update/run.py`, which may be relying on root being configured for its own output." That is
real, measured (three `logging.getLogger(__name__).warning(...)` calls that lose their formatting),
and genuinely a fourth module. So #2678 is **narrowed to exactly that remainder** rather than closed
outright: retitled to name `scripts/update/run.py`, its body rewritten to record that #2643's PR
absorbed the `log_rotate` half, with a comment linking this PR. See Task 6.

### The central decision: `propagate = False`, matching the sibling

**Both watchdogs converge on the #1311 topology**: a pinned-name module logger at INFO with
`propagate = False`, carrying exactly one `RotatingFileHandler`, attached at the entry point. Root is
never touched by either module.

Evidence, in order of weight:

1. **The plist doubles.** `scripts/valor-service.sh:627-630` sends both `StandardOutPath` and
   `StandardErrorPath` to `logs/watchdog.log`. Any root StreamHandler is therefore a second write
   path into the same file. Round 1's shape (`basicConfig` deleted, but the handler on a propagating
   logger with root configured) writes every watchdog record twice, exactly as #1311 described.
2. **The sibling already ruled.** `monitoring/worker_watchdog.py:136-148` names this defect verbatim
   and fixed it with `propagate = False`.
3. **The control is clean.** `com.valor.worker-watchdog` is live on this machine under the identical
   plist shape; 13,861 lines, multiplicity histogram entirely 1.
4. **Round 1's counter-evidence is refuted.** Its only cited submodule record traces to
   `bridge_watchdog.py:869`.
5. **Submodule records change independently of this decision.** Their routing changes because
   `scripts/log_rotate.py` stops configuring root, not because the watchdog's logger stops
   propagating. `propagate = False` governs the watchdog's *own* records only. The two effects are
   separable and are reported separately in the Data Flow table.

**What an operator will see change**, stated here and required to appear in the PR body as a
decision, not a footnote. This list grew in revision 3 and now covers three modules:

- **`logs/watchdog.log`: one line where there used to be two**, for every record the bridge watchdog
  emits itself.
- **`logs/watchdog.log`: submodule WARNING+ records lose their `%(asctime)s %(levelname)s` prefix.**
  They still arrive, via `logging.lastResort` → stderr → the plist redirect, as bare messages.
- **`logs/watchdog.log`: submodule INFO records stop arriving.** Root is empty at WARNING, so they are
  dropped at the call site. This is a real loss and it is not mitigated; see Risk 1.
- Uncaught interpreter tracebacks still land in the file via the plist redirect, unchanged.
- `logs/worker_watchdog.log` does not change at all.
- `logs/log_rotate_error.log` does not change at all under the LaunchAgent — same format, same level,
  same stream, rehearsed.
- `/update`'s stderr changes: its three `logging` warnings lose their timestamp prefix, and unrelated
  libraries' INFO chatter stops leaking there.

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
- **`scripts/log_rotate.py` gets the same transformation, with the opposite propagation decision**:
  `logging.basicConfig(...)` moves verbatim into the existing `__main__` guard at `:190`;
  `logger = logging.getLogger("log_rotate")` stays at module scope and gains
  `logger.setLevel(logging.INFO)`; `propagate` stays at its `True` default, because this module
  configures **root**, not its own logger, and its plist writes stdout and stderr to two different
  files so there is no doubling to remove.
- **`tests/unit/test_watchdog_log_isolation.py`**: subprocess import probes per module, a scope-aware
  AST guard over the 14 guarded files (`monitoring/*.py` plus `scripts/log_rotate.py`), and positive
  tests locking format, level, path, rotation, idempotence, and each module's propagation topology.
- **Existing test edits**: three `TestLoggerConfiguration` cases rewritten, and four `caplog` sites
  across two files converted to the explicit-handler pattern. `tests/unit/test_log_rotate.py` needs
  **no changes** — verified: it asserts only on rotation behavior, constants, and
  `SELF_EXCLUDED_FILES`, and makes no assertion about logging configuration anywhere in its 180 lines.

### Flow

`launchd fires (60 s)` → **`python monitoring/bridge_watchdog.py`** → `__main__` guard calls
`_configure_logging()` → **module logger has one rotating handler, `propagate = False`** → `main()` →
`run_health_check()` emits → **`logs/watchdog.log`, one line per record**

`pytest imports monitoring.bridge_watchdog` → **module scope sets three attributes and opens nothing**
→ test calls `run_health_check()` → record reaches the explicitly attached `caplog.handler` →
**`logs/watchdog.log` never opened**

### Technical Approach

**1. `monitoring/bridge_watchdog.py`**

Act on **named statements, not a line range** (corrected at revision 4 — "delete lines 78-93" swept
up two lines the very next paragraph says to keep or rewrite):

- **DELETE** `logging.basicConfig(...)` (`:78-81`) — **deleted, not relocated**: nothing in the new
  shape configures root.
- **DELETE** `LOGS_DIR.mkdir(parents=True, exist_ok=True)` (`:86`).
- **DELETE** the `_watchdog_file_handler` construction and its `setFormatter` (`:87-92`).
- **DELETE** `logger.addHandler(_watchdog_file_handler)` (`:93`).
- **KEEP** `LOGS_DIR = PROJECT_DIR / "logs"` (`:85`) — an inert constant.
- **REPLACE** `:82`, `logger = logging.getLogger(__name__)`, with the pinned literal
  `logger = logging.getLogger("monitoring.bridge_watchdog")`. It must be re-bound, not deleted: under
  launchd the module runs as `__main__`, so `getLogger(__name__)` would return a different logger
  than the one the tests address. The Verification row
  `grep -c "getLogger(__name__)" monitoring/bridge_watchdog.py` → `0` is the gate that catches
  leaving it.

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

**Script-mode name pinning is airtight in all three modules.** Under launchd `__name__ == "__main__"`, so
`getLogger(__name__)` would return a *different* logger than the one tests address and than the one
`_configure_logging()` configures — producing an **empty** `logs/watchdog.log` in production, a quiet
catastrophic failure. Both watchdogs therefore bind the explicit string once at module scope and both
configure functions operate on that single module-level `logger` object, never constructing their
own reference. Verification greps for a surviving `getLogger(__name__)` in either file.

`scripts/log_rotate.py` is already safe on this axis and stays that way: it binds
`logging.getLogger("log_rotate")` — a pinned literal, not `__name__` — today at `:67`, and the fix
moves the line without touching the name. It is also structurally immune, because its entry point
configures **root**, which is the same object under every `__name__`. TC14 asserts
`logger.name == "log_rotate"` so the pinning cannot be quietly swapped for `__name__` later.

**3. `scripts/log_rotate.py`**

Replace lines 60-67 —

```python
# Use stderr for diagnostics — the LaunchAgent routes stderr to
# logs/log_rotate_error.log (which is self-excluded from rotation).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("log_rotate")
```

— with inert module-scope bindings:

```python
# Configuration lives in the __main__ guard (issue #2643): this module is
# imported as a library by scripts/update/log_cleanup.py, and configuring the
# importer's root logger at import time is the defect this issue removes.
# `propagate` stays at its True default on purpose — see the docstring note.
logger = logging.getLogger("log_rotate")
logger.setLevel(logging.INFO)
```

and move the call into the guard already present at `:190`:

```python
if __name__ == "__main__":
    # Use stderr for diagnostics — the LaunchAgent routes stderr to
    # logs/log_rotate_error.log (which is self-excluded from rotation).
    # basicConfig configures ROOT, so `logger` must keep propagate = True to
    # reach it. Setting propagate = False here silences this script entirely
    # (measured: exit 0, empty stderr, both INFO lines gone).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
```

`level`, `format`, and `stream` are byte-identical to today's call. `sys` is already imported at `:35`
and stays used by `sys.exit(main())`, so no import changes. The module docstring's self-rotation
paragraph remains accurate and needs one added sentence about where configuration now happens.

**Do not add `logger.propagate = True`.** It is the default; writing it is noise, and this repo's
other named loggers do not. The invariant is pinned by TC14 instead, so a cargo-culted
`propagate = False` fails a test rather than silently emptying `logs/log_rotate_error.log`.

**4. `tests/unit/test_watchdog_log_isolation.py`** (new)

Every case that calls a configure function runs inside a fixture that snapshots and restores the root
logger's level and handler list plus both named loggers' handlers, `propagate`, and `level`, and
`close()`s every handler it created. This module mutates process-global logging state; leaking it
corrupts sibling tests in the same xdist worker.

| Case | Assertion | The exact statement whose reintroduction/removal it catches |
|---|---|---|
| **TC1** | Fresh subprocess: after `import monitoring.bridge_watchdog`, `root.handlers == []` **and** `root.level == logging.WARNING`. **The absolute form, restored in revision 3** — it is satisfiable now that `scripts/log_rotate.py` is fixed in the same PR | any module-scope root mutation reachable from the bridge watchdog's import graph, including `basicConfig` at `bridge_watchdog.py:78` (which is no longer a no-op once log_rotate stops pre-configuring root) and `basicConfig` at `log_rotate.py:62`. **Measured RED on today's source** (`[<StreamHandler <stderr> (NOTSET)>] / 20`) and **measured GREEN under the fix** (`[] / 30`, obtained by neutralizing exactly the statements this plan deletes) |
| **TC1a** | Same subprocess, **`basicConfig` spy**: wrap `logging.basicConfig` in a recorder that captures `sys._getframe(1).f_code.co_filename` (the *immediate* caller, never the import stack), then `import monitoring.bridge_watchdog`. Assert no recorded caller is under `<repo>/monitoring/` **or** equals `<repo>/scripts/log_rotate.py` | the same two `basicConfig` statements as TC1, but it **names which module relapsed** instead of only reporting that root is dirty. Kept alongside TC1 for that attribution, not for extra coverage. **Measured RED on today's source**: the recorder returns exactly `['<repo>/scripts/log_rotate.py:62', '<repo>/monitoring/bridge_watchdog.py:78']`, both in the forbidden set. Green after the fix — measured: those are the only two `basicConfig` calls in the entire import graph |
| **TC2** | Same subprocess: no logger in `logging.root.manager.loggerDict` (plus root) holds a handler with `pathlib.Path(h.baseFilename).name == "watchdog.log"` — exact basename, not `endswith`, so a worker-side leak is never misattributed to the bridge. The iteration is guarded on both sides: skip entries that are not `isinstance(lg, logging.Logger)` (`loggerDict` also holds `logging.PlaceHolder` objects, which have no `.handlers`) and read the path via `getattr(h, "baseFilename", "")` (stream handlers have no `baseFilename`), so the probe reports a real leak rather than an `AttributeError` | `logger.addHandler(_watchdog_file_handler)` at `:93` (and the handler construction at `:87-92`) |
| **TC3** | Fresh subprocess: after `import monitoring.worker_watchdog`, `root.handlers == []` **and** `root.level == logging.WARNING`. Measured green today (`[] / 30`) — the worker's import graph never reaches `scripts.update` | a `logging.basicConfig(...)` or root `addHandler` reintroduced in the worker |
| **TC4** | Same subprocess: no handler anywhere has `pathlib.Path(h.baseFilename).name == "worker_watchdog.log"`, using TC2's identical `isinstance(lg, logging.Logger)` / `getattr(h, "baseFilename", "")` guards | `logger = _configure_logger()` at `worker_watchdog.py:162` |
| **TC5** | Scope-aware AST walk over an **explicit 14-file list** — every `monitoring/*.py` (13 files) plus `scripts/log_rotate.py`: no module-scope call to `logging.basicConfig`, to any `.addHandler`, to any `.mkdir`, or to `_configure_logger` / `_configure_logging`. Descends into module-level `try`/`with` bodies and into every module-level `if` **except the entry-point guard**; never enters `def`/`class` bodies. **The file set is built by a dynamic glob and asserted for *coverage*, not cardinality** — `files = sorted((REPO_ROOT / "monitoring").glob("*.py")) + [REPO_ROOT / "scripts" / "log_rotate.py"]`, then `assert len(files) >= 14, files` and `assert {p.name for p in files} >= {"bridge_watchdog.py", "worker_watchdog.py", "log_rotate.py"}, files`, printing the full resolved list on failure. **Revised at revision 4** from a frozen 14-entry list: a frozen list turns any future PR adding a clean `monitoring/*.py` into a failure of *this* test, pointing at logging isolation when the cause is an unrelated new file — the exact "fails for a reason unrelated to the code under test" shape Rabbit Holes rejects elsewhere. The glob is also strictly stronger, since it automatically covers a new `monitoring/*.py` that ships a module-scope `basicConfig`. Re-measured with the dynamic form: **5 hits over 14 files**, identical to the frozen form, so no detection power is lost | all three deleted bridge statements, the worker's module-scope configure call, **and `log_rotate.py:62`** — see the exemption note directly below for the measured hit sets in both directions |
| **TC5b** | AST assertion on the call site, per module: `_configure_logging` (bridge), `_configure_logger` (worker), and `logging.basicConfig` (log_rotate) are each called exactly once outside any function definition, and that call is a direct child of an `if __name__ == "__main__":` block. **TC5b does NOT inherit TC5's exemption** — it is a separate walker whose entire job is to look *inside* the guard. Sharing one scoping rule between the two is what round 3 rejected | moving the configure call to the top of `main()`. This is the only guard that catches that mutation — the import probes never call `main()`, and the entry-point proofs are green either way — and it is what keeps the five `main()` tests in `tests/unit/test_bridge_watchdog.py`, and `test_log_rotate.py`'s two `log_rotate.main()` calls at `:121` and `:166`, from reopening or reconfiguring a production log through a new door |
| **TC6** | Fresh subprocess: `bw.logger.propagate is False` and `bw.logger.level == logging.INFO` and `bw.logger.isEnabledFor(logging.INFO)` after a bare import | deletion of `logger.propagate = False`; deletion of `logger.setLevel(logging.INFO)` |
| **TC7** | Monkeypatch `bw.WATCHDOG_LOG_FILE` → `tmp_path/"nested"/"watchdog.log"`; `bw._configure_logging()`; emit one INFO record; flush; assert the file exists and its single line matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] probe$` | formatter-string change; removal of `parents=True` from the mkdir; a level regression |
| **TC8** | Same setup: after `_configure_logging()`, `logging.getLogger().handlers` is unchanged and holds no `_watchdog_owned` handler | attaching to root (`logging.getLogger().addHandler(fh)`) instead of the module logger — i.e. a relapse to round-1's shape |
| **TC9** | Call `bw._configure_logging()` twice, emit once, assert exactly one line in the file **and** exactly one **owned** handler (`[h for h in bw.logger.handlers if getattr(h, "_watchdog_owned", False)]`) on `bw.logger`. Ownership-scoped rather than a total count, so a `caplog.handler` left attached by an earlier test in the same worker cannot make it fail for an unrelated reason | removal of the owned-only clearing loop |
| **TC10** | Worker mirror of TC7 + TC8 + `wwd.logger.propagate is False`, via `wwd.LOG_FILE` and `wwd._configure_logger()`; asserts `maxBytes == 5 * 1024 * 1024` and `backupCount == 3` on the attached handler | worker formatter/rotation regression; a root relapse in the worker |
| **TC11** | Worker mirror of TC9 (idempotence) | removal of the worker's owned-only clearing loop |
| **TC12** | Monkeypatch `bw.WATCHDOG_LOG_FILE`, attach a sentinel unowned `logging.NullHandler()` to `bw.logger`, call `_configure_logging()`, assert the sentinel is still attached and not closed | a relapse from owned-only clearing to a blanket `for h in list(logger.handlers)` sweep, which would strip a test's `caplog.handler` |
| **TC13** | Fresh subprocess: after `import scripts.log_rotate`, `root.handlers == []` **and** `root.level == logging.WARNING` | `logging.basicConfig(...)` at `log_rotate.py:62`. **Measured RED on today's source**: the import moves root from `[] / 30` to `[<StreamHandler <stderr> (NOTSET)>] / 20`. **Measured GREEN under the fix**: `[] / 30`. This is the direct, unmediated gate on the newly in-scope module |
| **TC14** | Same subprocess: `log_rotate.logger.name == "log_rotate"`, `.level == logging.INFO`, and **`.propagate is True`** | *level clause:* deletion of the added `setLevel(logging.INFO)` — **measured RED today**, where the logger is NOTSET (`level == 0`) and only behaves as INFO because the `basicConfig` being removed set root to INFO. *propagate clause:* a cargo-culted `logger.propagate = False` copied from the watchdogs — measured to make script mode emit **nothing at all** (exit 0, empty stderr, both INFO lines gone), which no other guard in this plan detects. Green today and green after the fix, i.e. a pure regression guard, and Task 5 measures it flipping red under that mutation |
| **TC15** | **Fresh subprocess** (revision 4 — see the note below; it cannot run in-process). Inside the CHILD: `shutil.copy` the real `scripts/log_rotate.py` into a `tempfile.mkdtemp()/scripts/` tree (so `LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"` resolves under that temp dir, never a real checkout), `runpy.run_path(that_copy, run_name="__main__")` under `try/except SystemExit`, then emit `exit_code`, `root.handlers`, `root.level`, `handler.stream is sys.stderr`, `formatter._fmt`, and `logging.getLogger("log_rotate").propagate` as one JSON line on stdout. The parent asserts: exit code `0`; root holds exactly **one** `StreamHandler` with `stream is sys.stderr`, `formatter._fmt == "%(asctime)s %(levelname)s %(message)s"`; `root.level == logging.INFO`; `propagate is True`; and **some** line of the child's captured stderr matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO done: rotated=0 skipped=0$` — the child emits **two** INFO lines (`logs dir not found: <tmp>/logs` first, then `done: …`), so the assertion is `any(...)`, not "the line" | deleting the `basicConfig` outright instead of relocating it, or changing any of its `level` / `format` / `stream` arguments; and (jointly with TC14) the cargo-cult `logger.propagate = False`. **Rehearsed in the subprocess shape against the real fixed source text, with the PARENT's root deliberately dirtied first** (proving the child is unaffected): exit code `0`, `root.handlers == ['StreamHandler']`, `root.level == 20`, `stream is sys.stderr` True, `_fmt` exact, `log_rotate` logger level 20 / propagate True, stderr `2026-08-08 02:34:50,267 INFO logs dir not found: …` + `2026-08-08 02:34:50,267 INFO done: rotated=0 skipped=0`, and the parent's root handler list unchanged. **The temp-dir copy is mandatory, not stylistic, and survives the move to a subprocess**: `LOGS_DIR` is derived from `__file__`, so `runpy`-ing the real file — in a child just as much as in-process — rotates whatever `logs/` sits beside it, and against the main checkout that means rotating production logs over 10 MB, a `[DESTRUCTIVE]` operation this plan forbids |

TC2 and TC4 deliberately assert on **handler state** rather than on the shared file's byte count, so
they cannot flake when another agent's test run appends to the same file concurrently.

**TC5's entry-point-guard exemption (revision 4).** The fix relocates exactly the forbidden calls
into `if __name__ == "__main__":`, which *is* a module-level `if`. Without an exemption the walk
convicts correct code. The `ast.If` branch therefore skips the entry-point guard **before** the
recursive descent, and the skip covers `node.body` only — `orelse` bodies and every other
module-level `if` are still walked. **Corrected at revision 4** on two counts: the round-3 snippet
ended in a bare `continue`, which skips the whole `ast.If` node *including* its `orelse` and so did
not do what the prose says; and its predicate matched any comparison against `__name__`, which would
also exempt `if __name__ != "__main__":`. Tighten to the equality-against-`"__main__"` form and walk
the `orelse` explicitly:

```python
t = node.test
if (
    isinstance(t, ast.Compare)
    and isinstance(t.left, ast.Name)
    and t.left.id == "__name__"
    and len(t.ops) == 1
    and isinstance(t.ops[0], ast.Eq)
    and len(t.comparators) == 1
    and isinstance(t.comparators[0], ast.Constant)
    and t.comparators[0].value == "__main__"
):
    walk(node.orelse, path)   # body is exempt; the else-branch is not
    continue
```

Harmless on today's source either way — no guarded module puts an `else` on its `__main__` guard,
and the hit count is 5 under both forms — but the shipped walker now matches its description.

**Measured over the 14 files the glob currently resolves, in both directions.** *With* the exemption: **5 hits on
today's source** and **0 hits on fixed source**, so Task 4's "zero hits" is reachable and the build
can go green. *Without* it: **5 hits today** (identical set) and **3 hits on fixed source**. Each hit
names the exact statement whose presence it reports:

| Configuration | Source | Hits | Which statement each hit is |
|---|---|---|---|
| with exemption | today | **5** | `monitoring/bridge_watchdog.py:78` the module-scope `logging.basicConfig` this plan deletes; `:86` the module-scope `LOGS_DIR.mkdir`; `:93` the module-scope `logger.addHandler(_watchdog_file_handler)`; `monitoring/worker_watchdog.py:162` the module-scope `logger = _configure_logger()`; `scripts/log_rotate.py:62` the module-scope `logging.basicConfig` |
| with exemption | fixed | **0** | — |
| without exemption | today | **5** | identical set to the row above |
| without exemption | fixed | **3** | `monitoring/bridge_watchdog.py:1049 _configure_logging()`, `monitoring/worker_watchdog.py:874 _configure_logger()`, `scripts/log_rotate.py:189 logging.basicConfig(...)` — i.e. the three relocated calls the fix is *supposed* to produce, each a direct child of its `__main__` guard. This is the round-3 BLOCKER |

**TC5b is a separate walker and does not share TC5's exemption.** TC5b asserts the configure call
*is* a direct child of the `__main__` guard, so it must descend into exactly the block TC5 skips.
One shared scoping rule cannot serve both.

**TC1b is deleted in revision 3.** It measured root as a *delta* across the bridge watchdog's import,
which was the only shape available while `scripts/log_rotate.py` pre-configured root. TC1's restored
`root.handlers == []` strictly dominates it: every mutation TC1b caught (a module-scope root
`addHandler`, a root relapse carrying the watchdog's own formatter) fails TC1 as well, because TC1
rejects *any* handler on root. Keeping both would be one guard doing one job twice.

**Every subprocess probe pins its own environment; none inherits one.** `scripts/pytest-clean.sh`
sets no `PYTHONPATH` (verified: zero matches), and this repo has already been bitten by a shared
venv's `.pth` silently importing the main checkout from inside a worktree. TC1, TC1a, TC2, TC3, TC4,
TC13, TC14, **and TC15** therefore build the child environment explicitly rather than trusting a
human `export`:

```python
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
subprocess.run(
    [sys.executable, "-c", PROBE_SRC, str(REPO_ROOT)],
    cwd=REPO_ROOT,
    env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    capture_output=True, text=True, check=False,
)
```

and every probe's **first** assertion after the import is a checkout self-check, so a wrong-checkout
import fails loudly instead of reporting green about unmodified source:

```python
assert m.__file__.startswith(str(REPO_ROOT)), m.__file__
```

**The bridge's one-time entry-point proof (acceptance criteria 3 and 5) is a Task 5 step, not a test
case.** It runs the real `__main__` guard of the real file and therefore writes into whatever checkout
it runs in — as a permanent test it would reproduce the exact harm this issue exists to remove. Its
durable regression coverage lives in TC7 and TC10, which assert the same formatter, level, path, and
rotation against a monkeypatched `tmp_path`.

**`log_rotate`'s entry-point proof is different and *is* a permanent test (TC15)**, because
`runpy`-ing a temp-dir copy of the source has no production surface at all: `LOGS_DIR` is derived
from `__file__`, so the copy's rotation target is `<tmpdir>/logs`. The watchdogs cannot use that trick
— their log path comes from `PROJECT_DIR`, resolved from the real module location, which is why their
proof stays a one-time worktree step.

**TC15 runs in a subprocess, and this is forced, not stylistic (revision 4).** The relocated
`logging.basicConfig(...)` is a documented no-op once root already has handlers, and under pytest
root always does. Measured in this repo's venv, in-process: root is
`[_LiveLoggingNullHandler, _FileHandler /dev/null, LogCaptureHandler, LogCaptureHandler]` at level 30
both before and after the `runpy` call, zero stderr `StreamHandler`s are added, `root.level ==
logging.INFO` is False, and the `done: rotated=0 skipped=0` record goes to `caplog` rather than
stderr — all four assertions fail against *correct* code, and neither named mutation is
distinguishable. In a fresh child the same source produces the measured state in TC15's row above.
**Clearing `root.handlers` in-process is barred as an alternative**: it removes pytest's
`LogCaptureHandler` mid-test and breaks `caplog` for every sibling in the same xdist worker. Both
named mutations are measured red in the subprocess shape: `logger.propagate = False` yields exit 0
with **completely empty stderr** (both INFO lines gone, so the regex has no line to match) while root
state is otherwise identical, and deleting `basicConfig` entirely yields `root.handlers == []`,
`root.level == 30`, no `StreamHandler`, no `_fmt`, empty stderr.

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
      This is also why `--check-only` cannot serve as the real-invocation proof for acceptance
      criterion 5 — a branch that does not depend on logging cannot demonstrate that logging works.
      Task 5 uses the `__main__` guard directly instead.
- [ ] The four `caplog` assertions (`tests/unit/test_bridge_watchdog.py:753`, `:957`;
      `tests/integration/test_update_loop_wedge_recovery.py:156`, `:221`) are the error-state
      rendering checks for the watchdog's warning and critical paths. Under `propagate = False` they
      **will fail unmodified** — this is measured, not assumed (see Test Impact) — and each is
      converted to the explicit-handler pattern.

## Test Impact

**All four `caplog` sites re-verified at revision 2**, with their exact locations:
`tests/unit/test_bridge_watchdog.py:753` and `:957`, and
`tests/integration/test_update_loop_wedge_recovery.py:156` and `:221` — the latter file is under
`tests/integration/`, never `tests/unit/`. The remedy is proven rather than speculative:
`tests/unit/test_worker_watchdog.py` runs ten live instances of the
`logger.addHandler(caplog.handler)` + `try/finally` pattern against an already-`propagate = False`
logger. Acceptance criterion 4 holds under the edited tests.

**Measured fact behind the `caplog` edits.** pytest's `caplog` handler is attached to the **root**
logger and relies on propagation; `caplog.at_level(..., logger="name")` sets the named logger's level
but does not attach anything to it. A standalone probe confirms: with `propagate = True` caplog sees
the record; with `propagate = False` caplog sees **nothing**; with `propagate = False` plus
`lg.addHandler(caplog.handler)` in a `try/finally`, caplog sees it again. The workaround is already
in-repo at `tests/unit/test_worker_watchdog.py:275` and nine sibling sites. Resolving this by leaving
`propagate = True` is not an option — that reinstates the doubling.

- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_no_duplicate_handlers`
      — UPDATE. Today: `importlib.reload(wwd); assert len(wwd.logger.handlers) == 1`. After the fix a
      reload attaches nothing, so the assert becomes **zero owned handlers** —
      `assert not [h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]`, not
      `len(wwd.logger.handlers) == 0`. `logging.getLogger("monitoring.worker_watchdog")` is a
      process-global singleton and `importlib.reload` does not reset its `handlers` list, so a total
      count is coupled to whole-process ordering: a `caplog.handler` left by any of the ten sibling
      sites, or a `_configure_logger()` call from TC10/TC11, would make it non-zero for reasons
      unrelated to the code under test. Ownership scoping keeps the gate aimed at this module's own
      handlers, and it still fails if a reload starts attaching one. Then, with
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
- [ ] `tests/unit/test_worker_watchdog.py::test_heartbeat_threshold_env_override` (**:1063**) and
      `::test_heartbeat_threshold_default_is_180` (**:1072**) — VERIFY, no edit expected. Both
      `importlib.reload(wwd)` and assert only on `HEARTBEAT_THRESHOLD`. They re-open the real log on
      every reload today and stop doing so after the fix. Run and confirm rather than assume.
      **Revision-4 note:** #2683 (`fb00b8542`) added an autouse `_restore_worker_watchdog_constants`
      fixture to this file that snapshots and restores `_WWD_MODULE_SCALARS =
      ("HEARTBEAT_THRESHOLD",)` around every test, because these reloads were leaking a mutated
      constant into later tests (#2628). That fixture restores *module scalars only* — it does not
      touch `wwd.logger` or its handlers, so it neither fixes nor interferes with this plan's
      concern. Do not treat it as already having solved the reload-reopens-the-log problem.
- [ ] `tests/unit/test_worker_watchdog.py` — the ten `wwd.logger.addHandler(caplog.handler)` sites
      (**lines 294, 391, 421, 731, 751, 768, 792, 821, 845, 905** — all shifted +19 by `fb00b8542`)
      — VERIFY, no edit expected. They keep working, and the owned-only clearing change is what
      protects them from being swept.
- [ ] `tests/unit/test_bridge_watchdog.py::test_hibernating_logs_message` (caplog at :753) — UPDATE:
      wrap with `bw.logger.addHandler(caplog.handler)` / `removeHandler` in `try/finally`, matching
      `test_worker_watchdog.py:275`.
- [ ] `tests/unit/test_bridge_watchdog.py` caplog site at :957 (`[update-release]` CRITICAL) —
      UPDATE: same conversion.
- [ ] `tests/unit/test_bridge_watchdog.py` — the five `main()` calls (544, 571, 598, 781, 805) and
      the two `importlib.reload(bw)` sites (1106, 1114) — VERIFY, no edit expected. All five are
      `--check-only` asserting on `capsys` and never reach `_configure_logging()`; both reload sites
      assert only on `CRASH_STORM_THRESHOLD` / `WEDGE_DOMINANCE_FRACTION`.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` — UPDATE: same explicit-handler
      conversion against `monitoring.bridge_watchdog`'s logger, at the **two** current caplog sites.
      **Re-pointed at revision 4** — #2670 (`fd52fc648`) rewrote this file wholesale, so the
      round-3 coordinates (`:156`, `:221`, plus an inert `:60`) no longer exist and the inert third
      site is gone. The current sites, both of which read `caplog.records` and therefore both of
      which break under `propagate = False`:
      - `test_redis_exception_is_inconclusive(caplog)` — `caplog.at_level(logging.WARNING,
        logger="monitoring.bridge_watchdog")` at `:371`, `caplog.records` at `:376`
      - `test_unreadable_process_start_suppresses_verdict(caplog)` — `at_level` at `:387`,
        `caplog.records` at `:395`

      Both assert on WARNING-level messages (`bridge_update_flow_signal_unreadable` / `Redis error`,
      and `fail-safe` respectively). The conversion is mechanical and identical to the two
      `test_bridge_watchdog.py` sites. **Re-grep for `caplog` in this file before editing** rather
      than trusting these line numbers — it has been rewritten once already during this plan's life.
- [ ] `tests/integration/test_watchdog_recovery.py` — VERIFY, no edit expected. It imports
      `monitoring.worker_watchdog` at `:67` and `:138` but uses no `caplog` against `wwd.logger`.
      Absent from round 1 entirely; added here.
- [ ] `tests/conftest.py:834` (`"bridge_watchdog": "monitoring"`) — no change, and **confirmed
      inert**. It is an entry in `FEATURE_MAP`, consumed by `pytest_collection_modifyitems` to attach
      a `monitoring` marker based on the *test file's name*. It performs no import.
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestNoModuleLevelImports::test_bridge_watchdog_has_no_module_level_agent_session_import`
      — no change. It greps the source text for an unrelated `AgentSession` import.
- [ ] `tests/unit/test_log_rotate.py` (180 lines) — **VERIFY, no edit expected**, and this was checked
      rather than assumed. Every assertion in the file is about rotation behavior
      (`rotate_logs`, `sweep_oversized_backups`, `_rotate_one`), the module constants
      (`LOG_MAX_SIZE`, `LOG_BACKUP_HARD_CAP`), or `SELF_EXCLUDED_FILES`. There is no reference to
      `basicConfig`, `logger`, `handlers`, `propagate`, or `caplog` anywhere in it. Two cases call
      `log_rotate.main()` (`:121`, `:166`) with `rotate_logs` / `sweep_oversized_backups`
      monkeypatched; `main()` does not configure logging before or after this change, so both keep
      passing. The file's own `logger.info` emissions become silent under an unconfigured root, which
      no assertion depends on. **Run and confirm rather than assume.**
- [ ] `tests/unit/test_update_log_cleanup.py` — VERIFY, no edit expected. It imports
      `from scripts import log_rotate` at `:9` for its constants and monkeypatches
      `sweep_oversized_backups`; it asserts on the returned `LogCleanupResult`, never on log output.
- [ ] `tests/unit/test_update_log_rotate_agent.py` — no change. Despite the name it tests
      `service.install_log_rotate_agent()` (plist installation), and never imports
      `scripts.log_rotate`.
- [ ] **New coverage for the newly in-scope module**: TC13, TC14, TC15 in
      `tests/unit/test_watchdog_log_isolation.py`, each with a named mutation and both directions
      measured. TC13 and TC14's level clause are demonstrated-red on today's source; TC14's propagate
      clause and TC15 are regression guards whose red direction Task 5 measures under mutation.
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
- **Widening the AST guard beyond its 14 named files.** The guard covers `monitoring/*.py` plus
  `scripts/log_rotate.py` — the three modules this PR fixes and their package siblings. A repo-wide
  guard would fail on day one: `bridge/telegram_bridge.py:566` has the same module-scope
  `logging.basicConfig`, as do roughly twenty `scripts/migrate_*.py` files, `scripts/debug_catchup.py`,
  and `scripts/backfill_memory_titles.py`. Those belong to other workstreams and other issues. TC5's
  file set is a dynamic glob over `monitoring/*.py` plus `scripts/log_rotate.py`, asserted for
  **coverage** (`len >= 14` and a required-names subset) rather than exact cardinality, so the
  guard's advertised breadth matches its real breadth and a newly added `monitoring/*.py` that ships
  a module-scope `basicConfig` is caught automatically — while a clean new module does not fail this
  test for an unrelated reason. The plan states the limit rather than implying coverage it lacks.
- **A session-scoped autouse fixture asserting `logs/watchdog.log` never grows.** It asserts on a file
  every concurrent agent in this checkout may write, so it would flake for reasons unrelated to the
  code under test, and it fails at session teardown where attribution is hardest.
- **An env-var override for the log path.** A module-level `Path` constant that tests `monkeypatch`
  gets the same result with no new configuration surface, no `.env` entry, and no
  `config/settings.py` field.
- **Adding any second write path into `logs/watchdog.log`** (a handler on root gated on `__main__`, a
  `logging.Filter`, a custom `lastResort`) **to win back the submodule INFO records**. Something *is*
  lost here — see Data Flow row 3 and Risk 1 — and it is still not worth a second path, which would
  reinstate exactly the doubling this issue removes for the watchdog's own records. The submodule
  records the bridge watchdog actually cares about are its own; the rest are incidental chatter from
  `scripts.update.*` that reached the file only because a log rotator configured root. If a specific
  submodule record turns out to be operationally necessary, the fix is to log it from the watchdog's
  own logger, not to reopen root.
- **Configuring logging in `scripts/update/run.py`'s `__main__` guard.** It is the correct fix for
  `/update`'s three now-unformatted warnings, it is genuinely one call, and it is genuinely a fourth
  module in a PR the coordinator scoped to three. It is what **#2678** is narrowed to.
- **Cleaning the historical synthetic lines out of `logs/watchdog.log`.** Explicitly forbidden by
  acceptance criterion 5 and by this plan's No-Gos. The file is evidence.

## Risks

### Risk 1: `logs/watchdog.log` changes in three ways, two of which are losses
**Impact:** An operator reading `logs/watchdog.log` after this ships sees (a) one line where they used
to see two for the watchdog's own records, (b) submodule WARNING+ records arriving as bare messages
with no `%(asctime)s %(levelname)s` prefix, and (c) submodule INFO records gone entirely. (b) and (c)
follow from `scripts/log_rotate.py` no longer configuring root — they are consequences of the root
cleanup, not of `propagate = False`. Someone grepping for a count, or a log parser keyed on the
timestamp prefix, could break.
**Mitigation:** State all three as decisions, not side effects. The PR body carries the Data Flow
table verbatim under a top-level "Behavior change" heading, the explicit reading of acceptance
criterion 3, and the bounds: uncaught tracebacks unchanged, `logs/worker_watchdog.log` unchanged,
`logs/log_rotate_error.log` unchanged. `docs/features/watchdog-log-isolation.md` records the same.
Reversal is one `git revert`.
**Not mitigated, and deliberately so:** the submodule INFO loss is real and this plan adds no
mechanism to win it back (see Rabbit Holes). Nothing in the watchdog's own alerting depends on them.
**The loss is now enumerated rather than estimated, and it is overwhelmingly a win.**
`com.valor.bridge-watchdog` **is** installed and running on this machine, so `logs/watchdog.log` is
production output and the survey earlier revisions said they could not run has now been run. Of the
24,144 submodule-INFO-class lines in the current file, **22,230 (92.1%) are the single record
`INFO libssl detected, it will be used for encryption`**, one per 60-second tick from a transitively
imported crypto library. That one record is the dominant term in the file's ~145 KB/day growth and
therefore in the rollover cadence that evicts genuine incident history from the 5-backup retention
budget — the harm this issue exists to stop. The remaining ~1,900 submodule records are WARNING+ and
are **not** lost; they survive via `lastResort`, unformatted (row 2).
**Framing requirement:** the PR body and `docs/features/watchdog-log-isolation.md` must name this
record and its share explicitly, so the operator reads a 90%+ line-volume drop in `logs/watchdog.log`
as the intended outcome rather than as a stopped watchdog. This does **not** reopen the Rabbit Hole:
no second write path is added, only the characterisation changes.
**Residual, re-pointed:** the "after this change" column of the Data Flow table remains a projection.
It cannot be observed under launchd until the PR merges, and it rests on the plist text, the measured
post-fix root state (`[] / 30`), and `lastResort`'s documented semantics. The *today* column is
measured production output.

### Risk 2: `caplog` assertions break under `propagate = False`
**Impact:** Four existing tests fail. The tempting "fix" is to flip `propagate` back to `True`, which
silently reinstates the doubling this change exists to remove.
**Mitigation:** Measured up front (see Test Impact), not discovered at build time. All four sites are
converted to `logger.addHandler(caplog.handler)` in a `try/finally`, the pattern already used at ten
sites in `tests/unit/test_worker_watchdog.py`. TC6 asserts `propagate is False` after a bare import,
so a future flip-back fails the suite rather than passing quietly.

### Risk 3: A module logger left at the mercy of root's level would silently drop INFO
**Impact:** Propagation never consults ancestor logger *levels*, so what matters is the emitting
logger's own effective level. A logger left at NOTSET with no handlers inherits root's level and
**discards** `logger.info(...)` at the call site — invisible to any `caplog` assertion that does not
call `set_level`.
**Scope, corrected twice and now active (revision 3):** revision 2 downgraded this to a general
hazard, because with `scripts/log_rotate.py` left alone root stayed at INFO regardless. That premise
is gone. **Measured**: after this change root really does return to `[] / 30` in every process that
imports any of the three modules. So a logger left at NOTSET in any of them would now genuinely drop
its own INFO records. This is a live hazard for `scripts/log_rotate.py` in particular, whose logger is
NOTSET today and works only by accident of the `basicConfig` being moved.
**Mitigation:** `logger.setLevel(logging.INFO)` at module scope in **all three** files — the two
watchdogs keep theirs, and `log_rotate` gains one. The loggers are then never at the mercy of root's
level in any process, however it was configured. TC6 asserts `isEnabledFor(logging.INFO)` for the
watchdogs after a bare import; TC14 asserts `level == logging.INFO` for `log_rotate`, and that clause
is **measured red on today's source** (`level == 0`). Any INFO assertion elsewhere must use
`caplog.at_level(logging.INFO, logger="…")`; the rule is stated in all three docstrings. Because the
change removes a process-global side effect from every xdist worker that imports any of the three,
the build runs `tests/unit/` once in full before the PR is opened, not only the affected files.

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
`.worktrees/watchdog-import-time-log-handler/`, whose `logs/` is a separate directory. **The build
writes nothing to the main checkout's `logs/watchdog.log`; it is not "read-only for the duration".**
That earlier phrasing was wrong in both directions and is corrected at revision 5:
`com.valor.bridge-watchdog` is installed here on a 60-second `StartInterval` and appends to that file
continuously throughout the build, and any concurrent agent's pytest run in the main checkout can
append to it too. The invariant the gate enforces is therefore **append-only**, not immutability:
the recorded prefix must still be present and the file must not shrink. That is exactly what Task 1's
prefix gate checks, and why it needs the rollover fallback — a legitimate rollover moves the recorded
prefix into `logs/watchdog.log.1` and is not a violation.

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
  **Gate:** at the start of Task 1, record the baseline with **absolute paths pinned to the main
  checkout**:

  ```bash
  for f in /Users/tomcounsell/src/ai/logs/watchdog.log \
           /Users/tomcounsell/src/ai/logs/worker_watchdog.log; do
    n=$(wc -c < "$f" | tr -d ' ')
    printf '%s %s %s\n' "$f" "$n" "$(head -c "$n" "$f" | shasum -a 256 | cut -d' ' -f1)"
  done > /tmp/watchdog-log-baseline.txt
  test "$(wc -l < /tmp/watchdog-log-baseline.txt)" -eq 2 || { echo BASELINE_INCOMPLETE; exit 1; }
  ```

  Verification re-checks the recorded **prefix**, from any cwd:

  ```bash
  while read -r f n h; do
    cur=$(wc -c < "$f" | tr -d ' ')
    [ "$cur" -ge "$n" ] || { echo "TRUNCATED $f"; exit 1; }
    [ "$(head -c "$n" "$f" | shasum -a 256 | cut -d' ' -f1)" = "$h" ] || { echo "REWRITTEN $f"; exit 1; }
  done < /tmp/watchdog-log-baseline.txt && echo LOGS_PREFIX_INTACT
  ```

  Current values: `watchdog.log` = 41322 bytes, whole-file sha256
  `2c3c2f2d467de6d3d00f59c39469760548dd96de221b3e07808fca7792df89de`.

  **Prefix preservation rather than whole-file equality, because one of the two files has a live
  writer.** `com.valor.worker-watchdog` is installed and running on this machine on a 90-second
  `StartInterval`, so `logs/worker_watchdog.log` legitimately grows during a multi-hour build:
  measured two whole-file hashes fifteen minutes apart with no watchdog work in between, and they
  differed. A whole-file `shasum -c` would report `FAILED` for a reason the builder did not cause —
  a second way of failing on a correct build, next to the relative-path defect this same gate had.
  The prefix hash is stable under append (measured identical across repeated reads) and still fails
  loudly on the two things the No-Go actually forbids: truncation (size shrinks) and rewriting
  (recorded prefix stops matching). `logs/watchdog.log` gets the same treatment, because until this
  PR merges, any concurrent agent's pytest run in the main checkout can still append to it — that is
  the bug being fixed.

  **Absolute paths are mandatory and this row is deliberately checkout-absolute while every
  neighbouring Verification row is worktree-relative.** `shasum` stores whatever path string it is
  handed and `-c` resolves it against the caller's cwd. A relative baseline written in the main
  checkout and verified from the worktree re-resolves onto the worktree's `logs/` — the directory
  Task 1 deliberately writes into — reporting `FAILED` on a correct build while never once inspecting
  the main checkout it exists to protect. Reproduced: relative baseline in one directory, `-c` from a
  sibling, `FAILED`, exit 1. Re-measured with absolute paths from `/tmp`: `OK` on both, exit 0.
  The two-line assertion guards the empty-baseline case (measured: `shasum -c` on an empty file
  prints "no properly formatted SHA checksum lines found" and exits 1 here, but the assertion costs
  nothing and does not depend on that behavior).

  sha256 rather than `wc -c`, because a same-length rewrite passes a byte count. Round 1's
  `git diff --name-only | grep "^logs/"` row is **deleted**: `.gitignore:168` and `:378` make it
  return 0 unconditionally, including after a truncation — the permanently-green shape this plan
  condemns elsewhere, and the same misaiming this row's absolute paths now correct.
- `[DESTRUCTIVE]` **No running of the watchdog's health-check path as a validation step.**
  `python monitoring/bridge_watchdog.py` (no flags) reaches `run_health_check()` →
  `execute_recovery()`, which restarts the bridge. `--check-only` stops earlier but still calls
  `check_bridge_health()` → `log_crash("bridge_dead_on_watchdog_check")`, which appends to
  `data/crash_history.jsonl` and calls `analytics.collector.record_metric` → SQLite **and production
  Redis**. An issue about synthetic watchdog evidence must not forge a crash event to validate
  itself. The only sanctioned invocation is Task 5's proof, which enters the `__main__` guard and
  exits at the argument parser.
- `[DESTRUCTIVE]` **No `runpy` of the real `scripts/log_rotate.py` outside a `tmp_path` copy.**
  `LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"` is derived from the module's own
  location, so running the real file's `__main__` guard rotates whatever `logs/` sits beside it —
  against the main checkout that renames every production log over 10 MB into a `.1` slot and shifts
  the existing backups. TC15 copies the source into a `tempfile.mkdtemp()/scripts/log_rotate.py`
  tree inside its child process precisely so its rotation target is `<tmpdir>/logs`. The subprocess
  move in revision 4 does **not** relax this: a child `runpy` of the real file rotates the real
  `logs/` exactly as an in-process one would. Rehearsed that way; never any other way.
- **No behavioral change to recovery, escalation, health-check, or rotation logic.** Only logging
  setup moves. `LOG_MAX_SIZE`, `LOG_MAX_BACKUPS`, `LOG_BACKUP_HARD_CAP`, and `SELF_EXCLUDED_FILES` are
  untouched.
- **No new mechanism to restore submodule INFO records.** See Rabbit Holes and Risk 1.
- **No logging configuration added to `scripts/update/run.py`.** Correct fix, fourth module, out of
  scope; it is what #2678 is narrowed to.

Nothing else is deferred. All three modules, their tests, the new guard, and the docs are in scope.

## Update System

No update system changes required. The change is three source files, three test files, one new test
module, and docs — all propagated by the ordinary `git pull` in `scripts/remote-update.sh`.

- No new dependencies, config files, secrets, or `config/settings.py` fields.
- The launchd plists are unchanged. `scripts/valor-service.sh:599-635` (bridge, `StartInterval` 60),
  `scripts/install_worker.sh:214-253` (worker, `StartInterval` 90), and the
  `com.valor.log-rotate` agent installed by `scripts/update/service.py:908`
  (`install_log_rotate_agent`, 30-minute schedule) keep pointing `StandardOutPath` /
  `StandardErrorPath` at the same log files, and all three still invoke their modules as scripts, so
  the `__main__` guard is reached on every tick. No reinstall needed.
- **`scripts/log_rotate.py` is itself part of the update path**, imported by
  `scripts/update/log_cleanup.py:17` during every `/update` run. The import becomes side-effect-free;
  `sweep_oversized_logs()` still returns the same `LogCleanupResult`, so `/update`'s reporting is
  unchanged. Its own diagnostics move from formatted stderr lines to the caller's configuration —
  see Data Flow.
- No migration for existing installations: the next launchd tick after the pull picks up the new code.
- Per this run's constraints the build does **not** restart any service. All three scripts are
  re-executed from scratch by launchd every 60 s / 90 s / 30 min, so a pull is sufficient.
- **Operator note for the deploy:** the first tick after this lands changes what
  `logs/watchdog.log` looks like on any machine where the bridge watchdog is installed — fewer lines
  in three distinct ways. Flag all three in the deploy notes so nobody reads the reduction as a
  stopped watchdog. `logs/log_rotate_error.log` and `logs/worker_watchdog.log` look exactly the same
  as before.

## Agent Integration

No agent integration required. Both watchdogs are launchd-run monitoring processes with no CLI entry
point in `pyproject.toml [project.scripts]`, no MCP surface, and no import path from
`bridge/telegram_bridge.py` or the worker. The agent's only interaction with this subsystem is
reading `logs/watchdog.log` — which is exactly the surface this change makes trustworthy, and it
needs no wiring.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/watchdog-log-isolation.md` — the invariant (logging is configured at the
      entry point, never at import), applied across all **three** modules; the watchdogs' shared
      `propagate = False` topology and why the plists' both-streams redirect forces it; `log_rotate`'s
      opposite `propagate = True` decision and the two-different-files plist that justifies it,
      including the measured fact that `propagate = False` there silences the script completely; the
      precise Data Flow table of what reaches `logs/watchdog.log` before and after, naming the
      submodule WARNING+ format loss and the submodule INFO loss; and how the 14-file AST guard
      enforces the invariant along with what it deliberately does not cover.
- [ ] Add a row for it to the `docs/features/README.md` index table.
- [ ] Update `docs/features/bridge-self-healing.md` — the watchdog section gains: every line the
      watchdog itself writes to `logs/watchdog.log` now comes from a real entry-point invocation and
      appears once rather than twice, and submodule records that used to ride root's handler now
      arrive unformatted (WARNING+) or not at all (INFO).

### Inline Documentation
- [ ] Docstring on `_configure_logging()` citing #2643, stating the three constraints a future editor
      must not break: called from `__main__` only; `WATCHDOG_LOG_FILE` read from the module global at
      call time; no root configuration.
- [ ] Update the `_configure_logger()` docstring in `monitoring/worker_watchdog.py` — keep the #1311
      explanation, add the #2643 sentence about the call site moving out of module scope and the
      clearing becoming owned-only.
- [ ] Update the `scripts/log_rotate.py` module docstring — one sentence recording that logging is
      configured in the `__main__` guard because the module is imported as a library by
      `scripts/update/log_cleanup.py`. The existing self-rotation paragraph naming
      `logs/log_rotate_error.log` as the LaunchAgent's `StandardErrorPath` stays accurate and must
      stay verbatim; `basicConfig` keeps `stream=sys.stderr`.
- [ ] Comment at the relocated `basicConfig` in `scripts/log_rotate.py` explaining why `logger` keeps
      `propagate = True` here while both watchdogs use `False` — this module configures **root**, so
      turning propagation off routes its records to `lastResort` at WARNING and silences every INFO
      line `main()` emits.
- [ ] Comment in `tests/unit/test_watchdog_log_isolation.py` explaining why TC2 / TC4 assert on
      handler state rather than file size (concurrent-writer flake), why the root-state gates must run
      in a subprocess (`basicConfig` is a no-op when root already has pytest's handlers) — TC15
      included, for that same reason — and why TC15 `runpy`s a temp-dir copy rather than the real
      `scripts/log_rotate.py` (whose `LOGS_DIR` is derived from `__file__` and would rotate
      production logs).

### Test Suite Index
- [ ] Add `tests/unit/test_watchdog_log_isolation.py` to the `tests/README.md` index with its feature
      marker.

## Success Criteria

- [ ] Running `tests/unit/test_bridge_watchdog.py` in the worktree produces zero new lines in that
      worktree's `logs/watchdog.log` (identical sha256 before and after).
- [ ] `import monitoring.bridge_watchdog` in a fresh interpreter leaves `logging.getLogger().handlers`
      **empty** and root at **WARNING**, and opens no file (TC1). The plain absolute form, restored in
      revision 3 and measured to hold under the three-module fix (`[] / 30`). No `basicConfig` call
      originates from `monitoring/` or from `scripts/log_rotate.py` during the import (TC1a), and no
      logger anywhere holds a handler on `watchdog.log` (TC2).
- [ ] `import monitoring.worker_watchdog` in a fresh interpreter leaves `logging.getLogger().handlers`
      empty, leaves root at WARNING, and opens no file (TC3, TC4).
- [ ] `import scripts.log_rotate` in a fresh interpreter leaves `logging.getLogger().handlers` empty
      and root at WARNING (TC13), and `log_rotate.logger` reports `level == logging.INFO` and
      `propagate is True` (TC14).
- [ ] After a bare import, both **watchdog** loggers report `propagate is False`,
      `level == logging.INFO`, and `isEnabledFor(logging.INFO) is True`. `log_rotate`'s logger reports
      `propagate is True` and `level == logging.INFO` — the opposite propagation decision, justified
      in Solution and pinned by TC14.
- [ ] A real entry-point invocation still writes to `logs/watchdog.log` with formatter
      `%(asctime)s [%(levelname)s] %(message)s`, level INFO, 10 MB × 5 rotation — proved by running
      the module's actual `__main__` guard in the worktree and asserting on the handler it attached
      plus one emitted record (Task 5). Not proved via `--check-only`: that branch emits nothing
      above DEBUG on a machine without a bridge, and it writes a synthetic crash event to
      `data/crash_history.jsonl` and to production Redis on the way.
- [ ] `monitoring/worker_watchdog.py` keeps 5 MB × 3 rotation and `propagate = False` after its
      entry-point configuration runs.
- [ ] `scripts/log_rotate.py` run as a script still configures root with `level=INFO`,
      `format="%(asctime)s %(levelname)s %(message)s"`, `stream=sys.stderr`, and still emits its
      diagnostics formatted — proved by TC15's subprocess probe against a temp-dir copy of the real
      source, so the
      LaunchAgent's `logs/log_rotate_error.log` output is byte-shaped identically to today.
- [ ] `tests/unit/test_log_rotate.py` and `tests/unit/test_update_log_cleanup.py` pass unmodified.
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
      full per-guard mutation → test mapping from Task 5.
- [ ] The PR body states all three operator-visible effects explicitly under a top-level "Behavior
      change" heading, carrying the Data Flow table verbatim and the reading of acceptance
      criterion 3: own-record de-duplication, submodule WARNING+ losing its format prefix, and
      submodule INFO being dropped.
- [ ] Issue **#2678** carries a comment linking this PR, and its title and body name only
      `scripts/update/run.py` (Task 6). **Stated as an end state at revision 4**, not as an edit to
      perform: the retitle and rescope already happened before this revision, so a criterion phrased
      as "it is retitled" would be ticked without work. Only the PR-link comment is new.
- [ ] The No-Go prefix gate prints `LOGS_PREFIX_INTACT`: neither main-checkout log shrank and
      neither one's recorded prefix changed.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-logging)**
  - Name: `watchdog-logging-builder`
  - Role: Write the failing tests first, then the three-module fix, then the test edits and docs
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
- **Before any code change**, post a comment on **#2643** recording that acceptance criterion 3's
  `python monitoring/bridge_watchdog.py --check-only` clause is replaced by an entry-point
  invocation. The issue text names `--check-only` literally, and this plan's own No-Gos forbid
  producing that evidence, so a reviewer gating on the issue would otherwise find a criterion with
  no evidence behind it and no record of why. State the reason in one line: `--check-only` returns
  from `main()` (`:1011`) before `run_health_check()` (`:903`), every INFO-or-above call on that
  branch is gated on state a bridgeless machine lacks, and `check_bridge_health()` (`:557`) calls
  `log_crash("bridge_dead_on_watchdog_check")` → `analytics.collector.record_metric` → SQLite and
  **production Redis**. Name the replacement explicitly:
  `sys.argv = ["bridge_watchdog.py", "--__entrypoint_probe__"]` +
  `runpy.run_path(..., run_name="__main__")`, asserting `SystemExit.code == 2`, then asserting on the
  attached `_watchdog_owned` handler and one emitted record.
- **Then**, record the anti-criterion baseline with absolute paths (see No-Gos for why relative
  paths silently aim at the wrong checkout). **Derive the checkout root — do not hardcode a
  username.** Revision 3 pinned the literal `/Users/tomcounsell/src/ai`, which does not exist on
  every machine that runs this pipeline (this checkout is `/Users/valorengels/src/ai`), and the
  `for` loop would report `BASELINE_INCOMPLETE` on a correct build:
  ```bash
  AI_MAIN=$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" rev-parse --show-toplevel)
  for f in "$AI_MAIN/logs/watchdog.log" "$AI_MAIN/logs/worker_watchdog.log"; do
    n=$(wc -c < "$f" | tr -d ' ')
    printf '%s %s %s\n' "$f" "$n" "$(head -c "$n" "$f" | shasum -a 256 | cut -d' ' -f1)"
  done > /tmp/watchdog-log-baseline.txt
  test "$(wc -l < /tmp/watchdog-log-baseline.txt)" -eq 2 || { echo BASELINE_INCOMPLETE; exit 1; }
  ```
  `AI_MAIN` must be captured in the MAIN checkout **before** the worktree is created, and the values
  are re-measured here rather than read from this plan (see the No-Gos gate: the revision-3 figures
  are stale and the file has since rotated).
  Nothing else in this plan may run against the main checkout's `logs/`.
- Create the worktree at `.worktrees/watchdog-import-time-log-handler/` on branch
  `session/watchdog-import-time-log-handler`, provision its `.venv` on the pinned interpreter, and
  `export PYTHONPATH=$PWD` in every shell.
- Write `tests/unit/test_watchdog_log_isolation.py` with **TC1-TC15** as tabulated in Technical
  Approach, including the snapshot/restore logging fixture. All fifteen — TC13, TC14, and TC15
  included — must exist **before** the red demo below is captured, because that output is the PR's
  evidence and the red/green table names all three.
- Run the red demo and capture output **verbatim** for the PR body:
  ```bash
  cd /Users/tomcounsell/src/ai/.worktrees/watchdog-import-time-log-handler
  export PYTHONPATH=$PWD
  ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q
  ```
  **Expected red set, stated case by case rather than as a range** — a plan that predicts "everything
  fails" and then sees a pass has no way to tell a weak guard from a correct one:

  | Red against unfixed source | Green against unfixed source (regression guards, not red-demo guards) |
  |---|---|
  | **TC1** (measured: root is `[<StreamHandler <stderr> (NOTSET)>] / 20` after the import, not `[] / 30`), **TC1a** (spy records both `scripts/log_rotate.py:62` and `monitoring/bridge_watchdog.py:78`), TC2, TC4, TC5 (5 hits), TC5b, TC6 (today `bw.logger.propagate is True`, `level == 0`), TC7, TC8, TC9, TC11, TC12, **TC13** (measured: `[<StreamHandler>] / 20`), **TC14's level clause** (measured: `log_rotate.logger.level == 0`) | TC3 (the worker's root is already clean, measured `[] / 30`), TC10 (the worker already has the right formatter, rotation, and `propagate = False` since #1311), **TC14's propagate clause** (`propagate is True` today and after), **TC15** (measured green today in the subprocess shape: today's module-scope `basicConfig` produces the same child root state under `runpy` — `['StreamHandler'] / 20`, `stream is sys.stderr`, exact `_fmt`, `propagate True`, both INFO lines on stderr. It guards against the relocation being botched, not against today's placement) |

  Each guard in the right-hand column is justified by a named mutation in Task 5's table, and Task 5
  measures it flipping red. A guard that is green in both columns and red under no mutation is
  decorative and must be rewritten or removed. If a left-column case passes, the test does not reach
  the defect — fix the test before touching the source.

  **Note on ordering.** TC1 and TC1a stay red until Task 4 lands, because `scripts/log_rotate.py:62`
  keeps root dirty even after the bridge fix in Task 2. That is expected, and it is exactly why
  log_rotate is in this PR: without Task 4 there is no way to make these two gates express acceptance
  criterion 2 in its plain form.
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
- Apply the named-statement edits from Technical Approach §1 (delete `:78-81`, `:86`, `:87-92`,
  `:93`; KEEP `:85`; REPLACE `:82` with the pinned-literal `getLogger`) — **not** a blanket
  "delete 78-93", which would also remove `LOGS_DIR` and the `logger` binding.
  Add `WATCHDOG_LOG_FILE`, the pinned-name `logger` with `setLevel(INFO)` and
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

### 4. Fix `scripts/log_rotate.py`

- **Task ID**: `build-log-rotate-fix`
- **Depends On**: `build-worker-fix`
- **Validates**: `tests/unit/test_watchdog_log_isolation.py` (TC1, TC1a, TC5, TC5b, TC13, TC14, TC15),
  `tests/unit/test_log_rotate.py`, `tests/unit/test_update_log_cleanup.py`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: builder
- **Parallel**: false
- Replace lines 60-67 with the inert bindings (`logger = logging.getLogger("log_rotate")`,
  `logger.setLevel(logging.INFO)`) and move `logging.basicConfig(...)` into the existing
  `if __name__ == "__main__":` guard at `:190`, preserving `level`, `format`, and `stream=sys.stderr`
  **verbatim**. Do **not** write `logger.propagate = True`; the default is correct and TC14 pins it.
- Move the "Use stderr for diagnostics — the LaunchAgent routes stderr to `logs/log_rotate_error.log`"
  comment with the call, and add the propagate-rationale comment. Add the one docstring sentence about
  configuration living in the guard. The self-rotation paragraph stays verbatim.
- Run `tests/unit/test_log_rotate.py` and `tests/unit/test_update_log_cleanup.py` and confirm both
  pass **with no edits**. If either needs an edit, stop: Test Impact predicts none, and a surprise
  there means the transformation did more than intended.
- Re-run `tests/unit/test_watchdog_log_isolation.py`; TC1, TC1a, TC13, and TC14's level clause should
  now flip green. Confirm the AST scan reports **zero** hits across all 14 guarded files — reachable
  only with TC5's entry-point-guard exemption in place (measured 0 with it, 3 without).
- Assert the module is not short, so a smaller green run cannot pass for a complete one:
  `PYTHONPATH=$PWD ./scripts/pytest-clean.sh --collect-only -q tests/unit/test_watchdog_log_isolation.py`
  must collect every case TC1-TC15 declares.
- Commit.

### 5. Green demo, mutation check, and real-invocation proof

- **Task ID**: `validate-watchdog-logging`
- **Depends On**: `build-log-rotate-fix`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run both red commands verbatim; capture the green output. The worktree log's sha256 must be
  **identical** before and after `tests/unit/test_bridge_watchdog.py`.
- **Real-invocation proof (acceptance criteria 3 and 5), in the worktree, writing only into the
  worktree's `logs/`.** Run the module's actual `__main__` guard and then assert on what that guard
  configured. Argparse is given an unrecognized flag, so the guard runs `_configure_logging()` and
  `main()` exits at the argument parser — before `check_bridge_health()` and before
  `run_health_check()`:

  ```python
  # subprocess, cwd = worktree root, PYTHONPATH = worktree root
  import logging, pathlib, re, runpy, sys, uuid
  REPO = pathlib.Path.cwd()
  LOG = REPO / "logs" / "watchdog.log"
  before = LOG.stat().st_size if LOG.exists() else 0

  sys.argv = ["bridge_watchdog.py", "--__entrypoint_probe__"]
  try:
      runpy.run_path(str(REPO / "monitoring" / "bridge_watchdog.py"), run_name="__main__")
  except SystemExit as e:
      assert e.code == 2, e.code          # argparse rejected the flag; no health check ran

  lg = logging.getLogger("monitoring.bridge_watchdog")   # process-global singleton
  owned = [h for h in lg.handlers if getattr(h, "_watchdog_owned", False)]
  assert len(owned) == 1
  h = owned[0]
  assert pathlib.Path(h.baseFilename) == LOG
  assert h.maxBytes == 10 * 1024 * 1024 and h.backupCount == 5
  assert h.formatter._fmt == "%(asctime)s [%(levelname)s] %(message)s"
  assert lg.level == logging.INFO and lg.propagate is False

  token = uuid.uuid4().hex
  lg.info("entrypoint probe %s", token)
  h.flush()
  new = LOG.read_text()[before:]
  lines = [ln for ln in new.splitlines() if ln]
  pat = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] entrypoint probe " + token + "$")
  assert lines and all(pat.match(ln) for ln in lines)
  assert LOG.read_text().count(token) == 1
  ```

  Rehearsed end to end against a stand-in module with the same guard shape: argparse exit code 2, one
  owned handler, `maxBytes 10485760 / backupCount 5`, `fmt %(asctime)s [%(levelname)s] %(message)s`,
  level 20, `propagate False`, 81 bytes written, single line
  `2026-08-08 01:03:53,742 [INFO] entrypoint probe abf58b92…`, token count 1. It fires on a machine
  where the bridge is not installed, which is the machine this build runs on.
- **`--check-only` is NOT used as the proof, and its Verification row is deleted.** Two independent
  reasons. First, it cannot fire: `main()` returns at `monitoring/bridge_watchdog.py:982` before
  `run_health_check()` (`:860`), and every INFO-or-above call reachable on that branch is gated on
  state this machine lacks (`:591` needs `active_claude_count > SOFT_INSTANCE_LIMIT`; `:604` and
  `assess_update_flow` need `if running`; the zombie lines `:482`/`:494`/`:500` need
  `running and logs_fresh`), so the handler opens the file and writes zero bytes. Second, and worse,
  it is not side-effect free: with the bridge down, `check_bridge_health()` (`:514`) calls
  `log_crash("bridge_dead_on_watchdog_check")`, which appends to `data/crash_history.jsonl` **and**
  calls `analytics.collector.record_metric`, which writes to SQLite and **production Redis**. An
  issue about synthetic watchdog evidence must not manufacture a synthetic crash event to prove
  itself. Running `run_health_check()` for real is barred outright: it reaches `execute_recovery()`,
  which restarts the bridge.
- The "each new record appears exactly once" claim is **not** made from a shell invocation. The
  second copy comes from the plist's `StandardErrorPath` redirect under launchd, which a shell run
  does not have. The de-duplication claim rests on the plist text at `scripts/valor-service.sh:627-630`
  (`StandardOutPath` key/value then `StandardErrorPath` key/value; `:626` is the `StartInterval`
  value `<integer>60</integer>`, which revision 3 mis-cited)
  plus the `logs/worker_watchdog.log` control (13,861 lines, multiplicity histogram entirely 1), and
  the plan says so rather than staging a local run that proves nothing.
- Run the full `tests/unit/` suite once (Risk 3). This is the one run in this plan that is not
  narrowly scoped, and it is the one run that uses the **repo default** rather than `-n0`:
  ```bash
  PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/ -q
  ```
  `pyproject.toml:195` pins `addopts = "--tb=short -p no:postgresql -n auto --dist=loadfile
  --timeout=420 --timeout-method=thread"`, and CLAUDE.md's "roughly 20 minutes" figure describes
  exactly that parallel default — so **expect roughly 20 minutes for this command**. `--dist=loadfile`
  already isolates by file, which is the granularity the logging-leak risk operates at: module-scope
  import side effects are per-worker-process. `-n0` stays the flag for the four narrowly scoped files
  elsewhere in this plan, where it costs seconds; forcing `-n0` here would serialize the whole tree
  into one process and blow past the 20-minute budget by a large multiple, and CLAUDE.md both warns
  "a long-running suite is not stuck" and forbids pattern-killing pytest.
- **Per-guard mutation check (#2658).** One at a time, reintroduce each statement below, confirm the
  named test flips red, then revert. A mutation no test catches means that guard is decorative and
  must be rewritten or removed.

  **Both directions are required for every row.** Confirm the named guard FAILS on the mutation
  **and** PASSES against the corrected source. A guard that only satisfies one half is not a guard;
  round 2 of the critique caught three of them in this plan by checking the second half.

  | Mutation | Must turn red | Must be green on correct code |
  |---|---|---|
  | `logging.basicConfig(level=logging.INFO, ...)` at bridge module scope | **TC1** (root becomes `[<StreamHandler>] / 20`; no longer a no-op once log_rotate is fixed), **TC1a** (spy records `monitoring/bridge_watchdog.py`), TC5 | yes — measured: with all three fixed, root is `[] / 30` after the import and the spy records zero callers |
  | `logging.getLogger().addHandler(logging.StreamHandler())` at bridge module scope | **TC1** (root is non-empty), TC5 | yes — measured `[] / 30` |
  | `logger.addHandler(fh)` at bridge module scope | TC2, TC5 | yes |
  | `LOGS_DIR.mkdir(...)` at bridge module scope | TC5 | yes |
  | delete `logger.propagate = False` (bridge) | TC6 | yes |
  | delete `logger.setLevel(logging.INFO)` (bridge) | TC6 | yes |
  | `logging.getLogger().addHandler(fh)` inside `_configure_logging()` | TC8 | yes |
  | delete the owned-only clearing loop (bridge) | TC9 | yes |
  | blanket `for h in list(logger.handlers)` sweep (bridge) | TC12 | yes |
  | restore `logger = _configure_logger()` at worker module scope | TC3, TC4, TC5 | yes |
  | `logging.getLogger().addHandler(fh)` inside `_configure_logger()` | TC10, `test_configure_logger_does_not_touch_root` | yes |
  | delete the owned-only clearing loop (worker) | TC11, `test_logger_no_duplicate_handlers` | yes |
  | change either formatter string | TC7, TC10, and the entry-point proof's `formatter._fmt` assertion | yes |
  | change bridge `maxBytes`/`backupCount` | TC7's handler assertions, and the entry-point proof | yes |
  | change worker `maxBytes`/`backupCount` | TC10 | yes |
  | move `_configure_logging()` from the `__main__` guard to the top of `main()` | **TC5b** (the call-site AST assertion). Named explicitly because no runtime probe catches it: the import probes never call `main()`, and the entry-point proof stays green either way | yes |
  | restore `logging.basicConfig(...)` at `scripts/log_rotate.py` module scope | **TC13** (root becomes `[<StreamHandler <stderr> (NOTSET)>] / 20` — measured, this is today's state), **TC1** (the bridge's root is dirty again through the `:72` import chain), **TC1a** (spy records `scripts/log_rotate.py`), TC5 | yes — measured `[] / 30` for both TC13 and TC1 under the fix |
  | delete `logger.setLevel(logging.INFO)` (log_rotate) | **TC14's level clause** (logger falls back to NOTSET, `level == 0` — measured, this is today's state) | yes — measured `level == 20` after the fix |
  | add `logger.propagate = False` (log_rotate) — the cargo-cult mutation | **TC14's propagate clause**, and **TC15** (script mode emits nothing: measured in the subprocess probe as exit 0 with completely empty stderr, both INFO lines gone, so the `done: rotated=0 skipped=0` regex assertion has no line to match, while root state stays otherwise identical) | yes — measured: `propagate True`, and script mode prints `… INFO logs dir not found: <tmp>/logs` then `2026-08-08 02:34:50,267 INFO done: rotated=0 skipped=0` |
  | delete `logging.basicConfig(...)` entirely instead of relocating it | **TC15** (measured in the subprocess probe: `root.handlers == []`, `root.level == 30`, no `StreamHandler`, no `_fmt`, empty stderr) | yes — measured: root ends `['StreamHandler'] / 20`, `stream is sys.stderr`, `_fmt == "%(asctime)s %(levelname)s %(message)s"` |
  | change log_rotate's `level` / `format` / `stream` argument | **TC15** (asserts all three explicitly on the child's reported state) | yes — rehearsed against the real fixed source text in the subprocess shape |
  | move log_rotate's `basicConfig` from the `__main__` guard to the top of `main()` | **TC5b** (its log_rotate leg). No runtime probe catches it: `tests/unit/test_log_rotate.py:121` and `:166` call `main()` directly, and both would then reconfigure root mid-suite | yes |
- Confirm the prefix gate prints `LOGS_PREFIX_INTACT` for both main-checkout logs (No-Gos).

### 6. Documentation

- **Task ID**: `document-feature`
- **Depends On**: `validate-watchdog-logging`
- **Assigned To**: `watchdog-logging-builder`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/watchdog-log-isolation.md` carrying the Data Flow table and both propagation
  decisions; add the `docs/features/README.md` row; add the sentences to
  `docs/features/bridge-self-healing.md`; add the test module to `tests/README.md`.
- **Verify issue #2678 is already retargeted, then comment.** **Reworded at revision 4: the retitle
  and rescope have already happened**, so instructing a builder to perform them again produces
  either redundant edits or a criterion ticked without work. Confirmed live at revision 4 — #2678 is
  OPEN and already titled "scripts/update/run.py has no entry-point logging configuration and relied
  on log_rotate's import side effect", its body is already narrowed to `scripts/update/run.py`
  (naming `:236`, `:315`, `:381` and the guard at `:2365`), it already carries an "Already handled by
  #2643, not in scope here" section, and it already links this plan.
  The remaining work is therefore: **verify** that title and scope still hold, and **post a comment
  naming this PR** once a PR number exists, explaining that the `log_rotate` half is absorbed. The
  remainder #2678 retains is real — `run.py` has three `logging.getLogger(__name__).warning(...)`
  calls and no `basicConfig`, so after this PR they print unformatted via `lastResort`. Edit the
  title or body **only if the verification fails**. Do not close it.
- Commit.

### 7. Final validation and PR body

- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Assigned To**: `watchdog-logging-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm each Success Criteria checkbox against evidence, not intent.
- Assemble the PR body: red command + verbatim output, green command + verbatim output, the full
  mutation → test mapping table with its measured results, the sha256 pair, **and a top-level
  "Behavior change" section** carrying the Data Flow table verbatim, the explicit reading of
  acceptance criterion 3, all **three** operator-visible effects on `logs/watchdog.log`, the
  `/update` stderr note, and the statement that uncaught tracebacks, `logs/worker_watchdog.log`, and
  `logs/log_rotate_error.log` are unaffected.
- **Re-read the Data Flow table against the shipped code before pasting it.** It is published to the
  PR as the authoritative behavior statement, and revisions 2 and 3 both changed rows in it. A stale
  row here is a false claim in a permanent record.
- Confirm #2678's title and body name only `scripts/update/run.py` and that it carries the link
  comment.
- Confirm **criterion 3's invocation substitution is recorded on #2643** — the Task 1 comment
  replacing `--check-only` with the entry-point probe. Without it, a reviewer gating on the issue
  text finds an acceptance criterion this PR deliberately does not satisfy and no record of why.

## Verification

Zero-count `grep -c` rows exit 1 on no match. Run them with `|| true`, or outside `set -e`.

| Check | Command | Expected |
|-------|---------|----------|
| No `basicConfig` **call** anywhere in `monitoring/` | `grep -rEn "logging\.basicConfig\(" monitoring/ \| wc -l` | `0`. The open paren is the discriminator: `worker_watchdog.py:138` carries the bare words `logging.basicConfig` in the docstring the plan keeps, so the round-1 form (`grep -rn "logging.basicConfig"`) measured 2 today and 1 after a correct fix, never 0 |
| No module-scope `addHandler` in the bridge | `grep -c "^logger.addHandler\|^_watchdog_file_handler" monitoring/bridge_watchdog.py \|\| true` | `0` |
| No module-scope configure call in the worker | `grep -c "^logger = _configure_logger()" monitoring/worker_watchdog.py \|\| true` | `0` |
| No `getLogger(__name__)` in either watchdog (script-mode name pinning) | `grep -c "getLogger(__name__)" monitoring/bridge_watchdog.py monitoring/worker_watchdog.py \|\| true` | `0` for both |
| Both loggers are non-propagating at module scope | `grep -c "^logger.propagate = False" monitoring/bridge_watchdog.py monitoring/worker_watchdog.py` | `1` for both |
| No `basicConfig` **call** at `log_rotate` module scope | `grep -c "^logging.basicConfig(" scripts/log_rotate.py \|\| true` | `0`. **Column-anchored, not line-numbered**: the correct fix keeps exactly one `logging.basicConfig(` in the file, indented inside the `__main__` guard, so a bare `grep -c "logging.basicConfig("` reads `1` before and after and could never fire. The `^` is the discriminator. Measured both ways: `1` on today's source, `0` on the fixed rehearsal copy |
| log_rotate's `basicConfig` moved into the guard rather than deleted | `grep -c "^    logging.basicConfig(" scripts/log_rotate.py` | `1` — measured on the fixed rehearsal copy. Paired with the row above so "deleted entirely" fails one and "left at module scope" fails the other. Structural form is TC5b's log_rotate leg; runtime form is TC15 |
| log_rotate logger pinned and leveled at module scope | `grep -c '^logger = logging.getLogger("log_rotate")$' scripts/log_rotate.py; grep -c "^logger.setLevel(logging.INFO)$" scripts/log_rotate.py` | `1` for both |
| log_rotate stays propagating | `grep -c "logger.propagate" scripts/log_rotate.py \|\| true` | `0`. The bare word `propagate` appears at `:181` in a comment about exit codes, so `logger.` is the discriminator — measured `0` today. Runtime form is TC14 |
| log_rotate stderr format preserved | `grep -c '"%(asctime)s %(levelname)s %(message)s"' scripts/log_rotate.py` | `> 0` |
| log_rotate still writes to stderr | `grep -c "stream=sys.stderr" scripts/log_rotate.py` | `> 0` |
| log_rotate rotation constants untouched | `grep -c "LOG_MAX_SIZE = 10 \* 1024 \* 1024\|LOG_MAX_BACKUPS = 3\|LOG_BACKUP_HARD_CAP = LOG_MAX_SIZE \* 10" scripts/log_rotate.py` | `3` |
| log_rotate tests pass unmodified | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_log_rotate.py tests/unit/test_update_log_cleanup.py -n0 -q` and `git diff --stat -- tests/unit/test_log_rotate.py tests/unit/test_update_log_cleanup.py` | exit code 0; empty diff |
| Isolation tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_watchdog_log_isolation.py -n0 -q` | exit code 0 |
| Bridge watchdog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py -n0 -q` | exit code 0 |
| Worker watchdog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/unit/test_worker_watchdog.py -n0 -q` | exit code 0 |
| Wedge-recovery caplog tests pass | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/integration/test_update_loop_wedge_recovery.py -n0 -q` | exit code 0 |
| Watchdog-recovery integration passes | `PYTHONPATH=$PWD ./scripts/pytest-clean.sh tests/integration/test_watchdog_recovery.py -n0 -q` | exit code 0 |
| Import is inert (single smoke check outside pytest) | `PYTHONPATH=$PWD python -c "import logging; import scripts.update.service, scripts.log_rotate as lr, monitoring.bridge_watchdog as b, monitoring.worker_watchdog as w; r=logging.getLogger(); assert r.handlers==[] and r.level==logging.WARNING, (r.handlers, r.level); assert all(lg.propagate is False and lg.level==logging.INFO and not lg.handlers for lg in (b.logger, w.logger)); assert lr.logger.propagate is True and lr.logger.level==logging.INFO and not lr.logger.handlers; print('IMPORT_INERT')"` | output contains `IMPORT_INERT`. **Back to the absolute form in revision 3** — with all three modules fixed, root really is `[] / 30` after importing the whole graph (measured). It imports `scripts.update.service` deliberately, so the transitive chain that used to dirty root is exercised rather than avoided. Round 1's two separate inline rows re-implemented TC1-TC4 line for line and are deleted; this is the one standalone smoke check kept |
| Bridge rotation settings preserved | `grep -c "maxBytes=10 \* 1024 \* 1024" monitoring/bridge_watchdog.py` | `> 0` |
| Bridge backup count preserved | `grep -c "backupCount=5" monitoring/bridge_watchdog.py` | `> 0` |
| Worker rotation settings preserved | `grep -c "maxBytes=5 \* 1024 \* 1024" monitoring/worker_watchdog.py` | `> 0` |
| Worker backup count preserved | `grep -c "backupCount=3" monitoring/worker_watchdog.py` | `> 0` |
| Bridge log format preserved | `grep -c "%(asctime)s \[%(levelname)s\] %(message)s" monitoring/bridge_watchdog.py` | `> 0` |
| Worker log format preserved | `grep -c "%(asctime)s \[%(levelname)s\] %(message)s" monitoring/worker_watchdog.py` | `> 0` |
| Entry point configures and writes | the Task 5 real-invocation proof, run from the worktree with `cwd` and `PYTHONPATH` pinned to it | exit 0; one `_watchdog_owned` `RotatingFileHandler` on `monitoring.bridge_watchdog` at the worktree's `logs/watchdog.log`, `maxBytes=10485760`, `backupCount=5`, `_fmt == "%(asctime)s [%(levelname)s] %(message)s"`, logger level INFO, `propagate False`; the probe record appears once, matching the timestamped format |
| Configure call sits in the `__main__` guard | TC5b | passes for all three modules |
| Anti-criterion: production logs untouched | the No-Gos prefix gate over `/tmp/watchdog-log-baseline.txt` (absolute paths; this row is deliberately checkout-absolute while every other row here is worktree-relative, and prefix-scoped because `com.valor.worker-watchdog` appends to one of the two files every 90 s) | `LOGS_PREFIX_INTACT`, from any cwd |
| Feature doc exists | `test -f docs/features/watchdog-log-isolation.md && echo DOC_OK` | output contains `DOC_OK` |
| Feature doc indexed | `grep -c "watchdog-log-isolation.md" docs/features/README.md` | `> 0` |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

### Round 6 (recorded against plan revision `sha256:b411ddd4`, FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Roster gate 3/3, zero ungrounded.
Seven findings: 2 BLOCKERs, 3 CONCERNs, 2 NITs. Verdict: **NEEDS REVISION**.

Both blockers are freshness failures measured on the checkout the build will run in
(`/Users/valorengels/src/ai` @ `53826e95d`), not design failures. The three-module scope, both
propagation decisions, the entry-point relocation, TC1-TC15, and the `[DESTRUCTIVE]` No-Gos all
stand; the five defect sites (`bridge_watchdog.py:78`/`:86`/`:93`, `worker_watchdog.py:162`,
`log_rotate.py:62`) were re-read verbatim this round and are unchanged. What has changed is the
state of `logs/watchdog.log` and of the launchd agents on this machine, and the plan's own gate and
evidence base now describe neither correctly.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The No-Go prefix gate fails on a correct build. `logs/watchdog.log` is 9,693,507 bytes against a `maxBytes` of 10,485,760 (headroom 792,253) and grows ~1.07 MB/day (measured daily line counts 1,471 / 1,448 / 1,522 / 1,516 / 1,448 for Aug 11-15; backups `.1`-`.5` at ~10,486,xxx bytes dated Jul 22 / 18 / 14 / 10 / 7 give a ~4-day rollover cadence). Rollover is due within ~18 hours, and `[ "$cur" -ge "$n" ] \|\| echo "TRUNCATED"` reads a rollover as truncation, blocking Tasks 5 and 7. The plan notes the file is "approaching the 10 MB `maxBytes` rotation threshold" only as evidence of harm, never as a hazard to its own gate. | pending | Add a rollover fallback before declaring `TRUNCATED`: when `$(wc -c < "$f")` is below the recorded `$n`, test the recorded prefix against the rolled file — `[ "$(head -c "$n" "$f.1" \| shasum -a 256 \| cut -d' ' -f1)" = "$h" ] && continue` — and fail only when neither `$f` nor `$f.1` carries it. Do NOT raise `maxBytes`: the rotation constants are an explicit No-Go, and a rollover is not a No-Go violation (history moves to `.1`); only the gate's reading of it is wrong. Apply the same branch to `logs/worker_watchdog.log`. |
| BLOCKER | Risk & Robustness | The Data Flow evidence base is inverted on this checkout. The plan states `com.valor.bridge-watchdog` is "not installed on this machine ... show only `com.valor.worker-watchdog`" and that `logs/watchdog.log` holds "zero launchd-produced lines — all 369 are test pollution". Measured: `com.valor.bridge-watchdog` IS in `launchctl list` and `~/Library/LaunchAgents/com.valor.bridge-watchdog.plist` exists (`StartInterval 60`, both streams → `/Users/valorengels/src/ai/logs/watchdog.log`); `com.valor.worker-watchdog` is in NEITHER. The file is 9.69 MB with 689 lines today carrying production records (`INFO Killed stale bridge process 2207`, `INFO Recovery successful`, `WARNING bridge_update_loop_wedged: …`). This falsifies the "1 copy" control experiment (`worker_watchdog.log` measured 8,708 lines, not 13,861, last written at the 03:02 nightly-test burst), Risk 1's "cannot enumerate which submodule INFO records production loses" residual, and Risk 5's "read-only for the duration" premise. Task 7 mandates pasting the Data Flow table verbatim into the PR as authoritative. | pending | Gate it at the top of Task 1 beside the log baseline: `launchctl list \| grep -c com.valor.bridge-watchdog`, `launchctl list \| grep -c com.valor.worker-watchdog`, and `ls ~/Library/LaunchAgents/ \| grep -E 'bridge-watchdog\|worker-watchdog'`, recording the result in the commit message. With the bridge watchdog live, the doubling claim gains a direct on-machine proof, but the "1 copy" control must move to whichever agent is actually running and Risk 1's residual must become a measured enumeration. Re-point the honesty caveat; do not delete it. |
| CONCERN | Risk & Robustness; Scope & Value (both flagged) | Risk 1 books the submodule-INFO loss as an unmitigated regression, but it is 94% noise and its removal is the change's largest retention win. Of 689 lines written on 2026-08-16, **647 are the single record `INFO libssl detected, it will be used for encryption`**, one per 60 s tick, reaching the file only via `log_rotate`'s root `basicConfig` plus the plist redirect. That record drives the ~1.07 MB/day growth and the ~4-day rollover that rolls genuine incident history out of the 5-backup budget — which the Problem section already argues is harm. | pending | Capture the composition in Task 1 with the baseline: `grep -c "libssl detected" logs/watchdog.log` over `wc -l logs/watchdog.log`, plus per-day counts from `cut -c1-10 logs/watchdog.log \| sort \| uniq -c \| tail`. Add one sentence to Risk 1 and to the PR "Behavior change" section naming the record and its share, so the operator reads a 90%+ volume drop as intended rather than as a stopped watchdog. This does NOT reopen the Rabbit Hole — no second write path is added; only the characterisation changes. |
| CONCERN | History & Consistency | The plan ships two implementations of one gate and points Verification at the broken one. The No-Gos block hardcodes `/Users/tomcounsell/src/ai/logs/watchdog.log`, which does not exist here (`/Users/valorengels/src/ai`), so `wc -c` fails and the two-line assertion fires `BASELINE_INCOMPLETE` on a correct build — while Task 1 already carries the corrected `AI_MAIN=$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" rev-parse --show-toplevel)` form and condemns that literal. The same block's "Current values: 41322 bytes, sha256 `2c3c2f2d…`" is what the Freshness Check calls superseded and "must not be used"; both figures are stale again (measured 9,693,507 bytes, sha256 `670ffabb06a525468679687e8c72c6c926bb42b9abb7598ae9c520b7258b8eb2`). | pending | Delete the shell block and the "Current values" line from No-Gos, keeping only its prose rationale (absolute paths, prefix-not-whole-file, sha256-not-`wc -c`), and re-point the Verification anti-criterion row from "the No-Gos prefix gate" to Task 1's gate. Do NOT paste today's figures into No-Gos: at ~1.07 MB/day any literal baseline in the plan is stale before build starts, so Task 1's "re-measured here rather than read from this plan" must be the only contract. |
| CONCERN | History & Consistency | Four builder-facing task steps carry coordinates the plan's own Freshness Check already superseded, so the plan states one fact at two addresses and the executable half is wrong. Verified at `53826e95d`: Task 2's wedge-recovery caplog sites `:156`/`:221` do not exist (current: `caplog.at_level` at `:371` in `test_redis_exception_is_inconclusive` and `:387` in `test_unreadable_process_start_suppresses_verdict`); Task 3's reload tests are `:1063`/`:1072`, not `:1050`/`:1058`; Task 5's `--check-only` argument cites `:982`/`:860`/`:514`/`:482`/`:494`/`:500` where measurement gives `main()` at `:1011` returning at `:1062`, `run_health_check()` at `:903`, `check_bridge_health()` at `:557`, `kill_zombie_processes()` at `:512` — the same argument Task 1 makes with the corrected values; and Task 1's red-demo command is `cd /Users/tomcounsell/src/ai/.worktrees/…`, the hardcoded-username defect its own preceding bullet condemns. | pending | Mechanical sweep, no design impact: substitute `:371`/`:387` in Task 2, `:1063`/`:1072` in Task 3, `:1011`/`:903`/`:557`/`:512` in Task 5, and `"${AI_REPO_ROOT:-$HOME/src/ai}"` for both `/Users/tomcounsell` literals. Task 2 must also inherit Test Impact's "re-grep for `caplog` in this file before editing" instruction — that file has been rewritten once already during this plan's life. |
| NIT | History & Consistency | Two figures earlier rounds explicitly corrected survive in a revision marked `revision_applied: true`. The Solution still prices the rejected lazy import at "15 sites ... 10 sites", while the Freshness Check re-measures 6 lines and says "do not re-quote '15 sites'" (verified: 1 patch at `test_update_loop_wedge_recovery.py:75`, 5 constant reads at `test_bridge_watchdog.py:872, 886, 887, 889, 914`; the rejection still holds on the true figure). The exemption table's fixed-source hits still read `:1049`/`:874`/`:189` where round 5 measured `:1089`/`:876`/`:187`. | pending | n/a (NIT) — replace "15 sites"/"10 sites" with "6 lines: 1 patch and 5 constant reads", and either update the three fixed-source line numbers to round 5's values or drop the line numbers from that column, since the statement identities carry the meaning. |
| NIT | Scope & Value | Task 4 still says "Replace lines 60-67", a line-range edit of exactly the kind revision 4 replaced with named statements on the bridge ("Act on named statements, not a line range"). `scripts/log_rotate.py` has not drifted (`:62` `basicConfig`, `:67` `getLogger("log_rotate")` verified verbatim), so it is currently correct — but the asymmetry is a latent trap. | pending | n/a (NIT) — restate Task 4 in named-statement form: DELETE the module-scope `logging.basicConfig(...)`, KEEP and re-level `logger = logging.getLogger("log_rotate")`, MOVE the call plus its stderr comment into the existing `__main__` guard. |

### Round 4 (recorded against plan revision `sha256:fde16dbc`, FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Six findings: 1 BLOCKER, 2 CONCERNs,
3 NITs. The single blocker is a freshness failure, not a design failure: commit `fd52fc648` landed on
`main` at 2026-08-10 11:37 +0700 — after this plan's stated baseline `8877be374` and minutes before this
critique — and it rewrote `monitoring/bridge_watchdog.py` (+213) and
`tests/integration/test_update_loop_wedge_recovery.py` (486 lines changed). Everything the plan says
about `monitoring/bridge_watchdog.py:78`–`:93`, `monitoring/worker_watchdog.py`, `scripts/log_rotate.py`,
`tests/unit/test_bridge_watchdog.py` and `tests/unit/test_worker_watchdog.py` was re-measured this round
and still holds exactly. The three-module scope, both propagation decisions, the Data Flow table, the
`lastResort` reasoning, the log baseline gate, and both `[DESTRUCTIVE]` No-Gos all stand.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency, Risk & Robustness | The Freshness Check is now false. The plan's own verification command, re-run verbatim, returns `fd52fc648` ("Wedge detector: require evidence something was missed, and reset on restart (#2475) (#2670)", 2026-08-10 11:37 +0700) rather than being empty, and the section still reads "**Commits on main since issue was filed (touching referenced files):** none." Three measured consequences. (a) Every `monitoring/bridge_watchdog.py` citation below ~line 200 is wrong: `main()` is at `:1011` not `:968`; the entry-point guard is at `:1084-1085` not `:1041-1042`; `run_health_check()` at `:903` not `:860`; `check_bridge_health()` at `:557` not `:514`; `kill_zombie_processes()` at `:512`, so the INFO lines cited at `:482`/`:494`/`:500` have moved; `:591`, `:604`, `:932`, `:982`, `:1031` no longer hold what the plan says; and `:869-871` — the citation whose correction anchored reversing round 1's root-configuration decision — is now docstring prose. (b) Task 2 aims the caplog conversion at `tests/integration/test_update_loop_wedge_recovery.py` `:156` and `:221`, which no longer contain caplog code; the real sites are `:371` and `:387`, and Test Impact's third site `test_a_wedged_past_ceiling(caplog)` at `:60` **does not exist** — both real sites read `caplog.records` and both break under `propagate = False`. (c) The rejection of the lazy import at `bridge_watchdog.py:72`, which revision 3 says "stands unchanged", is priced at "15 sites" including "10" `patch("monitoring.bridge_watchdog.get_process_start_ts")` calls; that file now contains **1**, and the three symbols appear on **6** lines total across `tests/`. | **resolved (revision 4)** — Freshness Check re-baselined to `0babdb2ab` with a "Revision-4 re-verification" subsection carrying the corrected-coordinate table; Test Impact, Error State Rendering, and Task 2 re-pointed to `:371`/`:387`; the `test_a_wedged_past_ceiling` bullet deleted; the lazy-import rejection re-priced to the measured 6 lines. **Residual noted in-plan:** this critique's own "Re-verified UNCHANGED" list predates `fb00b8542` (2026-08-13) and its `tests/unit/test_worker_watchdog.py` addresses are themselves stale by +19; the revision-4 table supersedes them. Also re-measured: the No-Go log baseline (41322 B / `2c3c2f2d…` → 9692506 B / `51cfe6c4…`, and the 41322-byte *prefix* no longer matches either, so the file was rotated — the gate now re-measures at build start instead of hardcoding). | Set **Baseline commit** to `fd52fc648` and **Disposition** to "Changed (code)". Re-cite: `main()` `:968`→`:1011`; guard `:1041-1042`→`:1084-1085`; `run_health_check()` `:860`→`:903`; `check_bridge_health()` `:514`→`:557`; re-derive the `kill_zombie_processes` INFO lines from `:512`+ and the `--check-only` early return from `:1011`+. In Task 2 and Test Impact replace `(:156, :221)` with `:371` and `:387` (in `test_redis_exception_is_inconclusive` `:363` and `test_unreadable_process_start_suppresses_verdict` `:382`), and DELETE the `test_a_wedged_past_ceiling` bullet — the correct statement is "exactly two caplog sites in this file, both read `caplog.records`, both converted". A repo-wide grep for `logger="monitoring.bridge_watchdog"` / `logger="monitoring.worker_watchdog"` returns exactly four sites total, so the count of four is still right; only the addresses moved. Replace the "15 sites / 10 patch sites" pricing with the measured 6 lines (1 patch at `tests/integration/test_update_loop_wedge_recovery.py`, 5 constant reads at `tests/unit/test_bridge_watchdog.py:872,886,887,889,914`) and re-state whether the lazy-import rejection still follows — it still can, since the 5 constant reads alone break under it, but say so from the measured figure. Drop or re-locate the `:869-871` hibernation citation. **Re-verified UNCHANGED and still correct**: `monitoring/bridge_watchdog.py:78`, `:82`, `:86`, `:87-92`, `:93`; all `tests/unit/test_bridge_watchdog.py` citations (`main()` at 544/571/598/781/805, caplog at 753/957, reload at 1106/1114); all `tests/unit/test_worker_watchdog.py` citations (reloads 47/52/58/1050/1058, ten `addHandler(caplog.handler)` sites, `isolated_state` at `:34`); `monitoring/worker_watchdog.py:135`/`:150-152`/`:162`; `scripts/log_rotate.py:35`/`:58`/`:62`/`:67`/`:176`/`:190`; the 14-file / 5-hit AST baseline; the `[] / 30` → `[StreamHandler] / 20` root measurement; the post-fix `[] / 30` measurement; the `627-630` plist range; and the No-Go baseline (41322 bytes, sha256 `2c3c2f2d…`). This is a targeted re-baseline, not a re-plan. |
| CONCERN | Risk & Robustness | TC5's "file list is hard-coded and asserted to be exactly these 14" turns any future PR that adds a clean `monitoring/*.py` module into a failure of a watchdog-logging test, pointing at logging isolation when the cause is an unrelated new file. That is the "fails for a reason unrelated to the code under test" shape the plan itself rejects in Rabbit Holes for the session-scoped log fixture. The stated goal — advertised breadth matching real breadth — is met by a dynamic glob, which is strictly stronger because it automatically covers a new `monitoring/*.py` that ships a module-scope `basicConfig`, which a frozen list does not. | **resolved (revision 4)** — TC5 row now builds the set via `sorted((REPO_ROOT/"monitoring").glob("*.py")) + [log_rotate.py]` and asserts coverage (`len >= 14` plus a required-names subset) instead of cardinality, printing the resolved list on failure; the Solution-side sentence and the Risks-side restatement were updated to match. Re-measured 5 hits over 14 files, identical to the frozen form. | Build the set as `files = sorted((REPO_ROOT / "monitoring").glob("*.py")) + [REPO_ROOT / "scripts" / "log_rotate.py"]`, then assert coverage instead of cardinality: `assert len(files) >= 14, files` and `assert {p.name for p in files} >= {"bridge_watchdog.py", "worker_watchdog.py", "log_rotate.py"}, files`. Print the full resolved list in the failure message so real breadth stays visible. Re-measured with the dynamic form plus TC5's entry-point-guard exemption: **5 hits** over **14** files on today's source (`bridge_watchdog.py:78 basicConfig`, `:86 mkdir`, `:93 addHandler`, `worker_watchdog.py:162 _configure_logger`, `log_rotate.py:62 basicConfig`) — identical to the hard-coded form, so no detection power is lost. Keep the Rabbit Holes paragraph excluding `bridge/` and `scripts/migrate_*.py`. |
| CONCERN | Scope & Value | #2643's acceptance criterion 3 literally names `python monitoring/bridge_watchdog.py --check-only`. The plan deletes that Verification row and substitutes an unrecognized-flag `__main__` probe for two sound reasons, and it renegotiates the word "unchanged" under "Reading of acceptance criterion 3" — but it never renegotiates the *invocation*, and it records the substitution only inside the plan document. A reviewer gating on the issue text finds a criterion naming `--check-only` with no evidence for it, while the plan's own No-Gos forbid producing that evidence. | **resolved (revision 4)** — Task 1 gains a first bullet, before any code change, requiring a comment on #2643 that records the substitution, its one-line justification (re-derived to `main()` `:1011`, `run_health_check()` `:903`, `check_bridge_health()` `:557`), and the explicit `--__entrypoint_probe__` + `runpy.run_path` replacement; Task 7's confirmation list gains the matching check. | Add a Task 1 bullet, before any code change: post a comment on #2643 recording that criterion 3's `--check-only` clause is replaced by an entry-point invocation, with the one-line reason (that branch returns from `main()` before `run_health_check()`, every INFO-or-above call on it is gated on state a bridgeless machine lacks, and `check_bridge_health()` calls `log_crash("bridge_dead_on_watchdog_check")` → `analytics.collector.record_metric` → SQLite and production Redis). Name the replacement explicitly: `sys.argv = ["bridge_watchdog.py", "--__entrypoint_probe__"]` + `runpy.run_path(..., run_name="__main__")`, asserting `SystemExit.code == 2` then asserting on the attached `_watchdog_owned` handler and one emitted record. Add "criterion 3's invocation substitution is recorded on #2643" to Task 7's confirmation list. Re-derive the line numbers in that justification after the freshness re-baseline (`main()` `:1011`, `check_bridge_health()` `:557`). |
| NIT | History & Consistency | The TC5 entry-point-guard exemption's prose and its code snippet contradict each other. The prose says "the skip covers `node.body` only — `orelse` bodies and every other module-level `if` are still walked", but the snippet ends in `continue`, which skips the whole `ast.If` node including its `orelse`. Harmless today (no guarded module has an `else` on its `__main__` guard; re-measured 5 hits either way), but the shipped walker would not do what the plan says. | **resolved (revision 4)** — the snippet now walks `node.orelse` explicitly before `continue`, so it matches the prose, and the predicate is tightened to `len(t.ops)==1 and isinstance(t.ops[0], ast.Eq)` with the comparator asserted to be the constant `"__main__"`, so `if __name__ != "__main__":` is no longer exempted. | Either change the snippet body to `walk(node.orelse, path); continue`, or delete the "`orelse` bodies ... are still walked" clause from the prose. Also tighten the predicate to the entry-point guard specifically by adding `and isinstance(t.ops[0], ast.Eq)` and checking the comparator is the constant `"__main__"`, so `if __name__ != "__main__":` is not exempted. |
| NIT | History & Consistency | "Delete lines 78-93 outright" contradicts the paragraph immediately after it. Line `:85` is `LOGS_DIR = PROJECT_DIR / "logs"`, which the plan says to keep, and line `:82` is `logger = logging.getLogger(__name__)`, which must be re-bound to the pinned literal rather than deleted. | **resolved (revision 4)** — Technical Approach §1 and Task 2 both replaced the line range with named statements: DELETE `:78-81`, `:86`, `:87-92`, `:93`; KEEP `LOGS_DIR` at `:85`; REPLACE `:82` with `logger = logging.getLogger("monitoring.bridge_watchdog")`. | Replace the line range with named statements: delete `logging.basicConfig(...)` (`:78-81`), `LOGS_DIR.mkdir(parents=True, exist_ok=True)` (`:86`), the `_watchdog_file_handler` construction and its `setFormatter` (`:87-92`), and `logger.addHandler(_watchdog_file_handler)` (`:93`); KEEP `LOGS_DIR = PROJECT_DIR / "logs"` (`:85`); REPLACE `:82` with `logger = logging.getLogger("monitoring.bridge_watchdog")`. The Verification row `grep -c "getLogger(__name__)" monitoring/bridge_watchdog.py` → `0` is the gate that catches leaving it. |
| NIT | Scope & Value | Task 6's "Retarget issue #2678" is already done. Fetched live: #2678 is already titled "scripts/update/run.py has no entry-point logging configuration and relied on log_rotate's import side effect", its body is already narrowed to `scripts/update/run.py` (naming `:236`, `:315`, `:381` and the guard at `:2365`), it already carries an "Already handled by #2643, not in scope here" section, and it already links this plan. The task and its Success Criterion will be either redone redundantly or ticked without work. | **resolved (revision 4)** — Task 6 reworded to a verification-plus-comment step (edit only if verification fails; do not close), and the Success Criterion restated as the end state "#2678 carries a comment linking this PR and its title/body name only `scripts/update/run.py`". Re-confirmed live at revision 4 that the retitle/rescope already holds. | Reword Task 6 to a verification step: "Verify #2678 is already retitled to name `scripts/update/run.py` and scoped to that remainder; post the PR link as a comment once the PR number exists; edit title/body only if the verification fails." Reword the Success Criterion to the end state — "#2678 carries a comment linking this PR and its title/body name only `scripts/update/run.py`" — rather than to performing an edit that has already happened. |

Re-verified sound at round 4 by direct measurement rather than reading: the 14-file / 5-hit AST baseline
with TC5's exemption; root moving `[] / 30` → `[<StreamHandler <stderr>>] / 20` with log_rotate's
`%(asctime)s %(levelname)s %(message)s` format on `import monitoring.bridge_watchdog`, and staying
`[] / 30` for `monitoring.worker_watchdog`; the post-fix `[] / 30` claim, proved by neutralizing exactly
the two `basicConfig` statements this plan deletes and re-importing the whole graph including
`scripts.update.service`; `bw.logger` at `level 0 / propagate True` and `log_rotate.logger` at
`level 0 / propagate True` today (TC6 and TC14's level clause are genuinely red); the argparse-first
shape of `main()` that makes Task 5's entry-point proof exit 2 before any side effect;
`scripts/log_rotate.py` importing only stdlib, so TC15's temp-dir copy runs standalone and
`rotate_logs()` on a missing `<tmp>/logs` emits `logs dir not found` then `done: rotated=0 skipped=0`;
the two-different-files log-rotate plist (`com.valor.log-rotate.plist:25-28`) against both watchdog
plists' single-file redirect (`scripts/valor-service.sh:627-630`, `scripts/install_worker.sh:243-246`);
exactly four repo-wide `caplog.at_level(..., logger="monitoring.*_watchdog")` sites; exactly four
non-test-plus-test importers of the two watchdogs and three importers of `log_rotate`; TC12's "not
closed" assertion being mechanically checkable on the pinned Python 3.14 (`Handler.close()` sets
`self._closed = True`); and the No-Go baseline values, which still match byte for byte.

### Round 3 (recorded against plan revision `sha256:0c51697b`, FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Six findings: 2 BLOCKERs,
2 CONCERNs, 2 NITs. Both blockers are the shape round 2 caught this plan on twice — a gate that
fails against correct code — and both were measured in both directions before being written down.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | TC5's AST walk fails against correctly fixed source. Its predicate forbids module-scope calls to `logging.basicConfig`, any `.addHandler`, any `.mkdir`, and `_configure_logger` / `_configure_logging`, and the plan specifies that the walk "Descends into module-level `if`/`try`/`with` bodies". `if __name__ == "__main__":` is a module-level `if`, and the fix puts exactly those forbidden calls inside it in all three modules. Implemented literally and run against a byte-for-byte copy of the three files with only this plan's edits applied, the walk returns **3 hits on correct code** (`bridge_watchdog.py _configure_logging`, `worker_watchdog.py _configure_logger`, `log_rotate.py logging.basicConfig`), so Task 4's "Confirm the AST scan reports zero hits across all 14 guarded files" is unreachable. TC5b needs the opposite behaviour (it asserts the call sits inside the guard), so the two guards cannot share one scoping rule. | **Revision 4** — Technical Approach TC5 row, new "TC5's entry-point-guard exemption" note with the four-way measurement table, TC5b row ("does NOT inherit"), Task 4 zero-hits bullet. Re-measured independently: with the exemption 5 hits today / 0 on fixed; without it 5 today / 3 on fixed (`bridge_watchdog.py:1049`, `worker_watchdog.py:874`, `log_rotate.py:189`) | Give TC5's walker an entry-point-guard exemption, applied to `node.body` only and placed BEFORE the recursive descent, while still descending into every other module-level `if`/`try`/`with`: `t = node.test; if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__": continue`. Measured both directions with the exemption: **5 hits** on today's source (`bridge_watchdog.py:78 basicConfig`, `:86 mkdir`, `:93 addHandler`, `worker_watchdog.py:162 _configure_logger`, `log_rotate.py:62 basicConfig`) and **0 hits** on fixed source. Without it: 5 today, 3 on fixed. TC5b must NOT inherit the exemption. |
| BLOCKER | Risk & Robustness, History & Consistency | TC15 cannot pass in-process under pytest. It is specified to `runpy` a `tmp_path` copy of the fixed `scripts/log_rotate.py` "inside the root-logger snapshot fixture" and then assert root gained exactly one stderr `StreamHandler`, `root.level == logging.INFO`, an exact `formatter._fmt`, and a formatted line on captured stderr. Under pytest root already carries handlers, so the relocated `logging.basicConfig(...)` returns early. Measured in this repo's venv: root is `[_LiveLoggingNullHandler, _FileHandler /dev/null, LogCaptureHandler, LogCaptureHandler]` at level 30 both before and after the `runpy` call, **zero** stderr `StreamHandler`s are added, `root.level == logging.INFO` is False, and the `done: rotated=0 skipped=0` record goes to pytest's capture handler rather than stderr. All four assertions fail on correct code and neither named mutation is distinguishable. The plan states the governing rule twice ("`logging.basicConfig()` is a no-op when root already has handlers, and under pytest root always does") and applies it to TC1/TC3 but not to TC15; the subprocess-probe list omits TC15 deliberately. | **Revision 4** — TC15 rewritten as a fresh-subprocess probe; TC15 added to the subprocess-probe list; new "TC15 runs in a subprocess" note; `[DESTRUCTIVE]` No-Go, test-comment checklist, Success Criterion, and three Task 5 mutation rows re-worded to the subprocess shape. Rehearsed with the parent's root deliberately dirtied: child exit 0, `root.handlers == ['StreamHandler']`, level 20, `stream is sys.stderr`, exact `_fmt`, propagate True, two INFO lines on stderr, parent root untouched. Both mutations measured red in the child. In-process clearing of `root.handlers` explicitly barred | Move TC15 into a fresh subprocess like TC1/TC3/TC13 and add it to the subprocess list. Probe source: `shutil.copy` the real `scripts/log_rotate.py` into a `tempfile.mkdtemp()/scripts/` tree inside the CHILD, `runpy.run_path(copy, run_name="__main__")` under `try/except SystemExit`, then print `root.handlers`, `root.level`, `handler.stream is sys.stderr`, `formatter._fmt` and captured stderr as JSON; run via `subprocess.run([sys.executable, "-c", PROBE_SRC], cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, capture_output=True, text=True)`. Do NOT instead clear `root.handlers` in-process — that removes pytest's `LogCaptureHandler` mid-test and breaks `caplog` for every sibling in the same xdist worker. The `tmp_path`-copy requirement survives the move: `LOGS_DIR` is derived from `__file__`, so a subprocess `runpy` of the real file still rotates the real `logs/`. |
| CONCERN | Scope & Value | Revision 3 added TC13, TC14 and TC15 to the Technical Approach table, to Task 1's own red/green expectation table, to Task 4's Validates list, to Test Impact and to three Success Criteria, but no task instructs anyone to write them. Task 1 is the only authoring step for the new module and still says "with TC1-TC12 as tabulated". A builder following it literally ships twelve cases, after which Task 1's red demo cites three cases that do not exist, Task 4's "TC13, and TC14's level clause should now flip green" is vacuously satisfied, and the three `log_rotate` Success Criteria have no instrument behind them. | **Revision 4** — Task 1 authoring bullet now reads TC1-TC15 and requires all fifteen to exist before the red demo is captured; Task 4 gains a `--collect-only` count assertion | Change Task 1's authoring bullet to "TC1-TC15" and note in the same bullet that TC13/TC14/TC15 must exist before the red demo is captured, since that output is the PR's evidence. Add a collected-count assertion to Task 4 (`pytest --collect-only -q tests/unit/test_watchdog_log_isolation.py`) so a short module fails rather than producing a smaller green run. |
| CONCERN | Risk & Robustness | Task 5's "run it with `-n0` and expect roughly 20 minutes" mixes a serial flag with a parallel-run figure. `pyproject.toml:195` pins `addopts = "--tb=short -p no:postgresql -n auto --dist=loadfile --timeout=420 --timeout-method=thread"`, and CLAUDE.md's ~20-minute figure describes that default. `-n0` disables xdist and serializes the whole `tests/unit/` tree into one process, so the real wall clock is a large multiple. CLAUDE.md warns "a long-running suite is not stuck" and forbids pattern-killing pytest, so a validator working to a wrong budget is set up either to abandon the run or to reach for the one remedy the repo bans. | **Revision 4** — first option taken. Task 5's full-suite step now runs the repo default (`-n auto --dist=loadfile`, per `pyproject.toml:195`), which is what the ~20-minute figure describes; `-n0` is kept for the four narrowly scoped files and the bullet says why | Either run the full-suite pass with the repo default (`-n auto --dist=loadfile`, which already isolates by file and is the granularity the logging-leak risk operates at, since module-scope import side effects are per-worker-process) and keep `-n0` for the four narrowly scoped files, or keep `-n0`, measure the real duration once, and write that number into Task 5 with an explicit "do not kill this" note. |
| NIT | Risk & Robustness | TC2/TC4 iterate `logging.root.manager.loggerDict` and read `h.baseFilename`. `loggerDict` holds `logging.PlaceHolder` objects (no `.handlers`) alongside real loggers, and most handlers have no `baseFilename`, so the literal predicate raises `AttributeError` rather than asserting anything. | **Revision 4** — both guards folded into the TC2 row and referenced from TC4 | `for lg in list(logging.root.manager.loggerDict.values()) + [logging.getLogger()]: if not isinstance(lg, logging.Logger): continue`, then `pathlib.Path(getattr(h, "baseFilename", "")).name == "watchdog.log"`. |
| NIT | History & Consistency | The plist citation `scripts/valor-service.sh:626-630` is off by one: line 626 is `<integer>60</integer>` (the `StartInterval` value); the stream keys run 627-630. The range is the load-bearing evidence for the doubling claim and is quoted four times. | **Revision 4** — re-verified by line-numbered read and corrected to `627-630` | Verified by line-numbered read: 625 `<key>StartInterval</key>`, 626 `<integer>60</integer>`, 627 `<key>StandardOutPath</key>`, 628 `<string>${LOG_DIR}/watchdog.log</string>`, 629 `<key>StandardErrorPath</key>`, 630 `<string>${LOG_DIR}/watchdog.log</string>`. Change the range to `627-630` everywhere it appears. |

Everything else re-verified and found sound at round 3, by direct measurement rather than reading:
the two-`basicConfig` spy result (exactly `scripts/log_rotate.py:62` and `monitoring/bridge_watchdog.py:78`,
with root moving `[] / 30` to `[<StreamHandler <stderr>>] / 20`); the post-fix `[] / 30` claim across the
whole import graph including `scripts.update.service`; the 5-hit AST baseline; all 14 guarded files;
every `caplog`, `main()`, `reload` and constant-read line citation in the four test files; the
`log_rotate` two-different-files plist; `scripts/update/run.py`'s three warnings and zero `basicConfig`;
`tests/unit/test_log_rotate.py` at 180 lines with `main()` at `:121` and `:166`; the argparse-first
shape of `main()` that makes Task 5's entry-point proof exit 2 before any side effect; and the No-Go
baseline values (41322 bytes, sha256 `2c3c2f2d…`, 369 lines), which still match exactly.

### Revision 3 — the coordinator's ruling on blocker 1

Rounds 1 and 2 both treated `scripts/log_rotate.py:62` as somebody else's problem, and both were
wrong about it in the same way. Revision 2 resolved blocker 1 by weakening every bridge-side root
gate to a delta measurement and filing the root cause as #2678. Its reasoning about the *rejected*
alternative — a lazy import at `bridge_watchdog.py:72` — was correct and **stands unchanged**: those
three symbols are bound at 15 test sites, and removing them from the module namespace breaks
acceptance criterion 4.

But both options missed the obvious third one. `scripts/log_rotate.py` carries the identical defect,
sits directly in the bridge watchdog's import path, and already has a `if __name__ == "__main__":`
guard at `:190` waiting for the call. Leaving it is the half-migration Development Principle 1
forbids. **The coordinator ruled: fix it at source, in this PR.**

What changed as a consequence, all of it re-measured rather than reasoned:

| Consequence | Resolution |
|---|---|
| Acceptance criterion 2 becomes literally satisfiable | **Measured**: `import monitoring.bridge_watchdog` under the three-module fix leaves `root.handlers = []`, `root.level = 30`. TC1 restored to the absolute form on the bridge; TC1b deleted as strictly dominated. A spy confirmed there is **no fourth `basicConfig`** — exactly two callers in the whole graph, both fixed here |
| The `lastResort` claim flips back to TRUE for the bridge | **Measured**: `<_StderrHandler <stderr> (WARNING)>`, level 30. Submodule WARNING+ prints unformatted; submodule INFO prints nothing. Data Flow rows 2 and 3 rewritten as losses, and Research, Solution evidence 5, Risk 1, Risk 3, Architectural Impact, Rabbit Holes, and the Documentation tasks all corrected to match |
| The PR-body behavior-change section grows to three modules | Task 7 now requires all three `logs/watchdog.log` effects plus the `/update` stderr note, with the Data Flow table lifted verbatim and re-read against shipped code before pasting |
| TC1a / TC1b redundancy | TC1b deleted. TC1a kept and rescoped to both forbidden paths — it survives for caller *attribution*, not coverage, and is still measured red today (recorder returns both `log_rotate.py:62` and `bridge_watchdog.py:78`) |
| TC5's advertised breadth must match its real breadth | Widened to a **dynamic glob** over `monitoring/*.py` + `scripts/log_rotate.py` (14 files today), asserted for **coverage** rather than exact cardinality — `len >= 14` plus a required-names subset, resolved list printed on failure. **Revised at revision 4** from a frozen 14-entry list, which would have failed this test whenever an unrelated clean `monitoring/*.py` was added. Re-run under the glob: **5 hits**, identical. Rabbit Holes states plainly what it does not cover and why a repo-wide guard would fail on day one |
| #2678 must not describe shipped work | **Narrowed, not closed.** Its own closing line names the remainder — `scripts/update/run.py`, which has three `logging` warnings and no `basicConfig` of its own. Task 6 retitles it to that scope and comments the link |
| The plan must say three modules everywhere | Success Criteria, Key Elements, task list, file list, Update System, Architectural Impact, and Verification all updated. Task list grew from 6 to 7 steps |
| `log_rotate`'s own test coverage | `tests/unit/test_log_rotate.py` (180 lines) **needs no changes** — verified, it makes no assertion about logging configuration. TC13/TC14/TC15 added as new coverage, each with a named mutation and both directions measured |
| `propagate` for the dual library/script role | **`True`, kept at the default.** `log_rotate` configures ROOT via `basicConfig`, so propagation is the only path from its records to its handler — the exact inverse of the watchdogs, which configure their own logger and would double. Measured: `propagate = False` makes script mode emit **nothing at all**, silently emptying `logs/log_rotate_error.log`. TC14 pins it so the cargo-cult mutation fails a test |

Round 3's critique is the last. Every guard above was mutation-checked in both directions before this
revision was written: named statement, confirmed red on the mutation, confirmed green on correct
code. The second half is what round 2 caught this plan on twice.

**Revision 4 is a two-fix surgical patch, not a re-plan.** Round 3's two BLOCKERs were both "the gate
convicts correct code": TC5's walk descended into the very `__main__` guard the fix populates, and
TC15 asserted a root state that `basicConfig`'s no-op semantics make unreachable under pytest. Both
remedies were re-measured independently before being written in — TC5 four ways (with/without the
exemption × today/fixed source, over exactly the 14 guarded files) and TC15 three ways (fixed source
plus both named mutations, in the subprocess shape, with the parent's root deliberately dirtied to
prove child isolation). The two CONCERNs and both NITs are folded in. Nothing else in the plan
changed: the three-module scope, the #1311 topology, the Data Flow / `lastResort` table, the
entry-point proof, the log baseline gate, both `[DESTRUCTIVE]` No-Gos, and #2678's narrowing all
stand exactly as revision 3 left them.

### Rounds 1 and 2

Round 2 (recorded against plan revision `7897022ec`), all eight findings addressed in revision 2.
Round 1's nine findings were addressed in the previous revision and are recorded in git history at
`7897022ec`. Round 2 confirmed the `propagate = False` decision and all 12 file:line citations as
sound; every finding below was one of two shapes — a gate that cannot fire, or a gate that fails
against correct code. **Blocker 1's row below records revision 2's resolution, which revision 3
supersedes as described above; blockers 2 and 3 stand exactly as written.**

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, History & Consistency | Root is already configured by a module inside the bridge watchdog's own import graph, so every root-state gate in this plan fails on a correct build. `monitoring/bridge_watchdog.py:72` imports `scripts.update.service`, which reaches `scripts/update/__init__.py:10` → `scripts/update/run.py:24` → `scripts/update/log_cleanup.py:17` (`from scripts import log_rotate`) → `scripts/log_rotate.py:62` `logging.basicConfig(level=INFO, stream=sys.stderr)`. That runs BEFORE `bridge_watchdog.py:78`, so today's `basicConfig` at `:78` is already a no-op and deleting it changes nothing about root. Measured: after `import monitoring.bridge_watchdog`, root holds one `StreamHandler <stderr>` whose formatter is log_rotate's `%(asctime)s %(levelname)s %(message)s`, not the watchdog's bracketed format, and root level is 20 (INFO). Consequently TC1, the `IMPORT_INERT` Verification row, and Success Criterion 2 assert `root.handlers == []` and `root.level == WARNING` and will all FAIL after the fix lands. Task 4's mutation row "`logging.basicConfig(...)` at bridge module scope → TC1, TC5 must turn red" is half-decorative: reintroducing `basicConfig` cannot turn TC1 red, because `basicConfig` is a no-op once root has a handler. `monitoring/worker_watchdog.py` is unaffected — a spy probe on its import records zero `basicConfig` calls and a clean root. | **Revision 2** — Solution § "Root is already configured before line 78 runs"; Data Flow; TC1a/TC1b/TC3; `IMPORT_INERT` row; Success Criterion 2; Task 1 red-set table; Task 4 mutation table | Resolution chosen: **delta-measured gates**, not a lazy import at `:72`. The lazy import was priced and rejected — the three symbols are module attributes bound at 15 test sites (10 `patch("monitoring.bridge_watchdog.get_process_start_ts")` in `tests/integration/test_update_loop_wedge_recovery.py`, 5 constant reads in `tests/unit/test_bridge_watchdog.py`), so removing them from the namespace breaks acceptance criterion 4 and turns a logging fix into a namespace redesign. Every `root.handlers == []` / `root.level == WARNING` assertion is dropped on the bridge side and kept on the worker side, where root is measurably clean. The `basicConfig` mutation gate is repaired rather than downgraded: TC1a spies on `logging.basicConfig` and attributes by the immediate caller frame, which is **measured red on today's source** and immune to the no-op semantics. The pre-existing `scripts/log_rotate.py:62` contamination is documented in the plan and filed as **#2678**.  *Critic's suggested remedy, kept for the record:* Re-aim every bridge-side root assertion from absolute state to a delta this module owns. In TC1, import `scripts.log_rotate` (or `scripts.update.service`) FIRST inside the subprocess, snapshot `list(logging.getLogger().handlers)` and `logging.getLogger().level`, THEN `import monitoring.bridge_watchdog`, and assert both are byte-identical afterwards. Assert additionally that no root handler is `_watchdog_owned` and that no root handler's formatter `_fmt` equals `"%(asctime)s [%(levelname)s] %(message)s"` — that formatter string is the discriminator that survives the `basicConfig` no-op and is what a relapse would actually attach. Drop `root.handlers == []` / `root.level == WARNING` from TC1, `IMPORT_INERT`, and Success Criterion 2 for the bridge; TC3/TC4 may keep the absolute form since the worker's import graph is genuinely clean. In Task 4's mutation table, replace the `basicConfig at bridge module scope → TC1` cell with `→ TC5 only`, and add a mutation that CAN fire in-process: `logging.getLogger().addHandler(fh)` inside `_configure_logging()` → TC8. |
| BLOCKER | History & Consistency, Scope & Value | The No-Go anti-criterion gate `shasum -a 256 -c /tmp/watchdog-log-baseline.sha` records RELATIVE paths and is therefore resolved against the caller's cwd, so it does not protect the checkout the plan names. Task 1 writes the baseline in the MAIN checkout via `shasum -a 256 logs/watchdog.log logs/worker_watchdog.log`, which stores the literal strings `logs/watchdog.log` and `logs/worker_watchdog.log`. Every other Verification row is prefixed `PYTHONPATH=$PWD ./scripts/pytest-clean.sh`, i.e. run from the worktree, where `shasum -c` re-resolves those relative paths against the WORKTREE's `logs/` — the directory Task 1 deliberately writes to (`mkdir -p logs && touch logs/watchdog.log`, then a full `test_bridge_watchdog.py` run). Verified: writing a baseline in one directory and running `shasum -a 256 -c` from a sibling directory reports `FAILED` and exits 1. `logs/worker_watchdog.log` does not exist in a fresh worktree at all, so that entry reports "FAILED open or read". The row fails on a correct build, and the main checkout it claims to protect is never actually checked — the same permanently-misaimed shape as round 1's deleted `git diff \| grep "^logs/"` row. | **Revision 2** — No-Gos gate; Task 1 first bullet; Verification anti-criterion row | Baseline and `-c` both pinned to `/Users/tomcounsell/src/ai/logs/...`, with a two-line completeness assertion, and the row is labelled as deliberately checkout-absolute among worktree-relative neighbours. Re-measured: absolute baseline verified from `/tmp` reports `OK` on both, exit 0. One further defect found while re-measuring and fixed in the same pass: `com.valor.worker-watchdog` is live on this machine at a 90 s interval, so a whole-file hash of `logs/worker_watchdog.log` changes on its own (two reads fifteen minutes apart differed). The gate is therefore prefix-scoped — size-never-shrinks plus a stable recorded-prefix hash — which still catches truncation and rewriting without failing on a legitimate append.  *Critic's suggested remedy, kept for the record:* Record the baseline with ABSOLUTE paths so `shasum -c` is cwd-independent: `shasum -a 256 "$AI_MAIN/logs/watchdog.log" "$AI_MAIN/logs/worker_watchdog.log" > /tmp/watchdog-log-baseline.sha` where `AI_MAIN=$(git -C /Users/tomcounsell/src/ai rev-parse --show-toplevel)` captured in the MAIN checkout before the worktree is created. `shasum` stores whatever path string it is handed, so absolute in means absolute out and the check then resolves to the main checkout from any cwd. Guard against the empty-baseline failure too: assert the `.sha` file has exactly 2 lines before trusting an `OK`, since `shasum -c` on an empty file exits 0 and prints nothing. |
| BLOCKER | Risk & Robustness | The real-invocation proof cannot fire: `--check-only` emits no log record on the machine the build runs on, so `logs/watchdog.log` will not grow. `monitoring/bridge_watchdog.py:982` returns from the `--check-only` branch before `run_health_check()` (`:860`) is ever reached, and every INFO-or-above `logger` call in that branch's only callee `check_bridge_health()` (`:514`) is conditional on state this machine does not have: `:591` fires only when `active_claude_count > SOFT_INSTANCE_LIMIT`, `:604` and the whole `assess_update_flow` block are gated on `if running`, and `kill_zombie_processes()`'s INFO lines at `:482`/`:494`/`:500` require `running and logs_fresh`. With the bridge down (the plan's own Data Flow section states `com.valor.bridge-watchdog` is not installed here) the branch emits at most DEBUG, which INFO filters out. `_configure_logging()` opens the file in append mode and writes zero bytes. So Success Criterion 5 ("`--check-only` still writes to `logs/watchdog.log`") and Task 4's "confirm `logs/watchdog.log` grew ... and each new record appears exactly once" cannot pass. This also contradicts the plan's own Failure Path Test Strategy, which states `--check-only` "renders its report with `print()` to stdout and does not depend on logging configuration at all." Separately, "each new record appears exactly once" proves nothing about the defect even if a record did appear: the second copy comes from the plist's `StandardErrorPath` redirect under launchd, which a shell invocation does not have. | **Revision 2** — Task 4 real-invocation proof; Success Criterion 5; Verification (the `--check-only` row is deleted, replaced by an entry-point row); No-Gos `[DESTRUCTIVE]` bullet | The critique's suggested `runpy` run with no args is **rejected as unsafe**: it reaches `run_health_check()` → `execute_recovery()`, which restarts the bridge, and `check_bridge_health()` → `log_crash()` → `record_metric()`, which writes to production Redis. The proof instead enters the real `__main__` guard with an unrecognized flag, so `_configure_logging()` runs and `main()` exits at the argument parser; assertions are then made against the process-global `monitoring.bridge_watchdog` logger and one emitted record. Rehearsed end to end on a stand-in module: exit code 2, one owned handler, 10485760/5, exact format, single line, token count 1. The "exactly once" clause is dropped with an explanation that plist doubling is observable only under launchd.  *Critic's suggested remedy, kept for the record:* Replace `--check-only` with an invocation that actually reaches the logging path. Use `python -c "import runpy, sys; sys.argv=['bridge_watchdog.py']; runpy.run_path('monitoring/bridge_watchdog.py', run_name='__main__')"` from the worktree, which takes the `else` branch at `:1031` and calls `run_health_check()` → `:932` `logger.warning(f"Bridge unhealthy: ...")` fires unconditionally when the bridge is down, producing at least one formatted line. Assert the new line matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[(WARNING\|CRITICAL\|INFO)\] `. Keep `--check-only` as a separate no-crash smoke check with no assertion about file growth, and reword Success Criterion 5 to name the entry-point invocation rather than `--check-only`. Drop the "exactly once" clause from the shell-invocation proof and state plainly that plist doubling is only observable under launchd, so the de-duplication claim rests on the plist text plus the `logs/worker_watchdog.log` control, not on a local run. |
| CONCERN | Risk & Robustness | The Data Flow "Copies after this change" column and the whole `logging.lastResort` argument are wrong for the bridge watchdog process, and Task 6 requires that table to ship verbatim in the PR body as the stated behavior change. `lastResort` fires only when a record reaches a logger with NO handler anywhere in its ancestry, but root retains log_rotate's `StreamHandler` at level INFO in this process (see BLOCKER 1). So after the change: submodule WARNING+ records still arrive FORMATTED through root's handler rather than "unformatted via lastResort", and submodule INFO/DEBUG records still arrive (1 copy, not 0) because root stays at INFO. Risk 3's premise ("Root returns to its WARNING default in every process that imports either module") is false for the bridge. The change is therefore strictly smaller and safer than the plan advertises, but the PR body would ship a false table. | **Revision 2** — Data Flow (rewritten, with a separate worker paragraph); Research `lastResort` bullet; Solution evidence item 5; "What an operator will see change"; Risk 1; Risk 3 | Confirmed and corrected. `lastResort` is **FALSE for the bridge** and TRUE only for the worker. Rows 2–4 of the bridge table are now "unchanged", so exactly one row changes: own records 2 → 1. Risk 3's premise is scoped to a general hazard rather than a consequence of this diff.  *Critic's suggested remedy, kept for the record:* Re-derive rows 2 and 3 of the Data Flow table from the measured root state rather than from the assumption of an empty root. Row 2 becomes "1 copy, formatted with log_rotate's `%(asctime)s %(levelname)s %(message)s`"; row 3 becomes "1 copy" rather than 0. Delete the `lastResort` bullet from Research, from Solution evidence item 5, and from Risk 1's mitigation, and replace the "What an operator will see change" second bullet with the single true statement: own-records go from 2 copies to 1, and nothing else about the file changes. Reduce Risk 3 to a worker-only note, or delete it. |
| CONCERN | Risk & Robustness | The TC1-TC4 subprocess probes have no pinned interpreter, cwd, or environment, and `scripts/pytest-clean.sh` sets no `PYTHONPATH` (verified: zero matches in that script). The plan relies on a human `export PYTHONPATH=$PWD` in every worktree shell. A probe launched without it inherits the shared venv's `.pth`, silently imports the MAIN checkout's `monitoring/` package, and reports green about unmodified source — the worktree-isolation failure mode this repo has already been bitten by. | **Revision 2** — Technical Approach, "Every subprocess probe pins its own environment" | `cwd=REPO_ROOT`, `env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}`, `sys.executable`, and a first-assertion checkout self-check on `m.__file__`.  *Critic's suggested remedy, kept for the record:* Build the subprocess env explicitly rather than inheriting: `env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}` where `REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]`, and pass `cwd=REPO_ROOT` plus `sys.executable` to `subprocess.run`. Then add a self-check as the probe's first statement so a wrong-checkout import fails loudly instead of passing green: `import monitoring.bridge_watchdog as m; assert m.__file__.startswith(str(REPO_ROOT)), m.__file__`. |
| CONCERN | History & Consistency | The rewritten `test_logger_no_duplicate_handlers` asserts ZERO handlers on `wwd.logger` after `importlib.reload(wwd)`, which couples the test to whole-process ordering. `logging.getLogger("monitoring.worker_watchdog")` is a process-global singleton and `importlib.reload` does not reset its `handlers` list, so any earlier test in the same xdist worker that left a handler attached (a leaked `caplog.handler` from one of the ten sites at `tests/unit/test_worker_watchdog.py:275,372,402,712,732,749,773,802,826,886`, or a `_configure_logger()` call from TC10/TC11 in the new module) makes the count non-zero for reasons unrelated to the code under test. It fails far from its cause, which is the flake shape the plan already rejects in its Rabbit Holes. | **Revision 2** — Test Impact `test_logger_no_duplicate_handlers`; TC9 | Both switched to `[h for h in logger.handlers if getattr(h, "_watchdog_owned", False)]`.  *Critic's suggested remedy, kept for the record:* Assert on ownership rather than on the total count: `assert not [h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]` after the reload, and keep the exact-one assertion only for the paired explicit calls (`_configure_logger()` twice under a monkeypatched `LOG_FILE`, then `len([h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]) == 1`). Apply the same ownership-scoped form to the bridge in TC9. |
| NIT | Scope & Value | TC2's predicate "no handler whose `baseFilename` ends `watchdog.log`" also matches `worker_watchdog.log`, so TC2 and TC4 overlap and TC2 would misattribute a worker-side leak to the bridge. | **Revision 2** — TC2, TC4 | `pathlib.Path(h.baseFilename).name == "watchdog.log"` / `== "worker_watchdog.log"`.  *Critic's suggested remedy, kept for the record:* Compare the basename exactly: `pathlib.Path(h.baseFilename).name == "watchdog.log"` in TC2 and `== "worker_watchdog.log"` in TC4, rather than `str.endswith`. |
| NIT | History & Consistency | The Freshness Check states the scope-aware AST scan "reports module-scope hits at exactly `78 logging.basicConfig`, `86 LOGS_DIR.mkdir`, `93 logger.addHandler` and nowhere else in the package", but TC5's walk also looks for module-scope `_configure_logger` / `_configure_logging` calls, so the walk as specified additionally reports `monitoring/worker_watchdog.py:162`. Independently re-run and confirmed: the scan returns exactly those four hits across all thirteen `monitoring/*.py` files. Harmless (the fix deletes `:162` too, so TC5 still goes green) but the two sections describe different scans. | **Revision 2** — Freshness Check AST bullet; TC5 row | All four hits enumerated in both places, across 13 `monitoring/*.py` files; re-run at revision time.  *Critic's suggested remedy, kept for the record:* Add `monitoring/worker_watchdog.py:162 _configure_logger` to the Freshness Check bullet's enumerated hits so it matches TC5's stated predicate. |


### Round 5 (narrow verification pass — two gates only)

Scope: verify the two fixes revision 4 (`bfbcfe1fa`) claims, nothing else. Verdict: **READY TO BUILD
WITH CONCERNS**.

**GATE 1 — TC5's AST walk. CONFIRMED.** The walk was implemented exactly as the plan now specifies
(entry-point-guard exemption in the `ast.If` branch, before the recursive descent) and run over the
14-file list against today's source and against byte-for-byte copies carrying only this plan's edits.
Measured, matching the plan's table in all four cells:

| Configuration | Source | Hits |
|---|---|---|
| with exemption | today | **5** (`bridge_watchdog.py:78` basicConfig, `:86` mkdir, `:93` addHandler, `worker_watchdog.py:162` `_configure_logger`, `log_rotate.py:62` basicConfig) |
| with exemption | fixed | **0** |
| without exemption | today | 5 (identical set) |
| without exemption | fixed | **3** (the three relocated guard calls) |

Task 4's "zero hits" is therefore reachable. TC5b, run as a separate walker without the exemption,
passes on all three fixed modules and goes red on all three under the "move the configure call to the
top of `main()`" mutation (`total_outside_functions` drops to `[]`, `PASS=False`).

**GATE 2 — TC15. CONFIRMED.** TC15 is a genuine fresh-subprocess probe, it appears in the plan's
subprocess-probe list, the `tempfile.mkdtemp()` copy is made inside the CHILD, and the in-process
alternative is documented as barred. Rehearsed against the fixed source with the parent's root
deliberately dirtied first: child exit 0, exactly one `StreamHandler` with `stream is sys.stderr`,
`root.level == 20`, `_fmt == "%(asctime)s %(levelname)s %(message)s"`, `propagate is True`, both INFO
lines on the child's stderr with the `done: rotated=0 skipped=0` line matching, the parent's root
handler list unchanged, and the child's temp dir outside the repo. Red direction re-measured on both
named mutations: `logger.propagate = False` yields empty stderr with root state otherwise identical;
deleting `basicConfig` yields no handler, `root.level == 30`, no `_fmt`, empty stderr.

Both cheap concerns landed: Task 1's authoring bullet reads **TC1-TC15**, and Task 5's full-suite step
now runs the repo default (`-n auto --dist=loadfile`) with the ~20-minute figure, keeping `-n0` only
for the narrowly scoped files.

**Concerns to fold in at BUILD (neither blocks):**

| CONCERN | The exemption's bare `continue` also skips `node.orelse`, contradicting the plan's own prose ("the skip covers `node.body` only — `orelse` bodies and every other module-level `if` are still walked"). Measured: a forbidden call in the `else:` of the entry-point guard, or in an `elif` chained off it, returns **0 hits** where it should return 1. Every other module-level `if`, and module-level `try`/`with`/`except`, are still walked correctly, and `def`/`class` bodies are still never entered, so the 5/0 result on real source is unaffected. Fix at build is one line: walk `node.orelse` before the `continue`. |
| CONCERN | `fd52fc648` (#2475/#2670) landed on `main` after the plan's baseline and moved every `bridge_watchdog.py` citation below ~line 200. The fixed-source line numbers in the "without exemption / fixed" row are consequently stale (measured `bridge_watchdog.py:1089`, `worker_watchdog.py:876`, `log_rotate.py:187` against the plan's `:1049`, `:874`, `:189`), and Task 2's caplog conversion targets moved. Citation drift only; the design is untouched. Reconcile line numbers at build rather than trusting them. |
