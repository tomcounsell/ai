---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1844
last_comment_id:
revision_applied: true
---

# Worker Recycle: SIGKILL Instead of SIGABRT (No macOS Crash Dialogs)

## Problem

The standalone worker (`python -m worker`, entrypoint `worker/__main__.py`) protects itself with two hard-kill guards that recycle the process so launchd (`KeepAlive=true`) respawns a clean one:

- **Storm-cap** — too many background-task restarts inside a rolling window (`supervise()` done-callback).
- **Dead-man's-switch / watchdog** (#1815) — the worker is process-alive but its build loop is synchronously frozen (`_heartbeat_cycle`).

Both route through a single seam, `_self_kill()`, which today calls `os.abort()` → **SIGABRT** (signal 6). On macOS, SIGABRT is an abnormal termination that triggers the system crash reporter: a **"Python quit unexpectedly"** dialog plus a `Python-*.ips` report under `~/Library/Logs/DiagnosticReports/`.

Three unit tests spawn **real child processes** that deliberately fire the recycle to prove it is an unswallowable hard kill (not a catchable exception). Because the child dies via SIGABRT, macOS raises a crash dialog + `.ips` file on **every** test-suite run — manual (`pytest tests/`) and the nightly regression run (20:00 UTC). Root cause was confirmed via **8 `Python-*.ips` reports** whose timestamps all match test runs; the production `worker.log` shows the real worker healthy (it never aborts in production), so the dialogs are a pure test-harness artifact.

**Current behavior:** `_self_kill()` → `os.abort()` → SIGABRT on every recycle → macOS crash dialogs + `.ips` files on every test run.

**Desired outcome:** Zero macOS crash dialogs and zero new `.ips` files from test runs, while the three tests still prove the recycle is an unswallowable, signal-based process death that launchd would respawn — and production forensics are preserved (in fact upgraded).

## Freshness Check

**Baseline commit:** `abc66276cfb28628a852c5d926c7fbf493c6d870`
**Issue filed at:** 2026-07-02T05:32:29Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `worker/__main__.py:111-117` — `_self_kill()` body calls `os.abort()` — still holds (verified verbatim).
- `worker/__main__.py:177-190` — storm-cap done-callback reaches `_self_kill()` unconditionally — still holds.
- `worker/__main__.py:330-340` — dead-man's-switch stale-beacon path calls `_self_kill()` (gated by `WORKER_DEADMAN_ENABLED`) — still holds.
- `tests/unit/test_worker_supervisor.py:282-298` — asserts POSIX `returncode == -signal.SIGABRT` (-6), Windows `== 3` — still holds.
- `tests/unit/test_worker_deadman.py:416-421` — `test_self_kill_calls_os_abort` patches `os.abort` — still holds.
- `tests/unit/test_worker_watchdog.py:996-1016` — surviving-thread self-kill test patches `_self_kill` (behaviorally signal-agnostic) — still holds.
- Docs SIGABRT/`os.abort` mentions counted: `worker-fault-containment.md` (2), `worker-liveness-recovery.md` (5), `worker-service.md` (1) = 8 references to update.

**Cited sibling issues/PRs re-checked:**
- #1808 (wedged-worker investigation) — the forensics consumer; SIGABRT's `.ips` was one input. This plan preserves and upgrades that forensic value (see Technical Approach).
- #1815 (dead-man's-switch inversion) — introduced the second `_self_kill()` call site; unchanged.
- #1767 (off-loop heartbeat thread) — the rollback baseline; unchanged.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since` on the four affected files returns empty).

**Active plans in `docs/plans/` overlapping this area:** none touching `worker/__main__.py` recycle path.

**Notes:** All issue claims verified against baseline `abc66276`. No drift.

## Prior Art

No prior issues or merged PRs found for the SIGABRT/`os.abort` worker-recycle path (`gh issue list --state closed --search "SIGABRT os.abort worker recycle"` returned nothing). The seam and both call sites were introduced by #1816 (storm-cap) and #1815 (dead-man's-switch); neither revisited the signal choice. This is the first change to the kill mechanism itself.

## Research

No relevant external findings needed — proceeding with codebase context and platform knowledge. The relevant facts are OS-level and well established:

- **SIGKILL (signal 9)** cannot be caught, blocked, or ignored — identical unswallowability to SIGABRT — but macOS does **not** invoke the crash reporter for it, so no dialog and no `.ips` file.
- **`faulthandler.dump_traceback(all_threads=True)`** synchronously writes every thread's Python stack to a file (default `sys.stderr`); it is explicitly designed for "dump state right before dying" and works from the surviving off-loop heartbeat thread even when the asyncio event loop is frozen.
- launchd `KeepAlive=true` respawns the process on any death (SIGABRT, SIGKILL, or exit) — confirmed in the issue's Definitions table.

## Data Flow

The kill path is single-seam by design:

1. **Trigger (storm-cap)**: `supervise()._done_callback` — restart count in the rolling window reaches `max_restarts` → logs CRITICAL → calls `_self_kill()`.
2. **Trigger (dead-man's-switch)**: `_heartbeat_cycle()` on the off-loop heartbeat thread — beacon stale beyond `WORKER_DEADMAN_STALENESS_THRESHOLD` (or never ticked past startup grace) AND `WORKER_DEADMAN_ENABLED` → logs CRITICAL → calls `_self_kill()`.
3. **Seam**: `_self_kill()` — today `os.abort()`; after this change, emits a full thread dump then delivers SIGKILL.
4. **Output**: process dies uncatchably → launchd respawns a clean worker; forensic thread dump lands in `logs/worker_error.log` (`faulthandler` writes to stderr; `com.valor.worker.plist` `StandardErrorPath` routes stderr there).

The change is confined to step 3. Steps 1, 2, and 4 are untouched — the trigger logic, the CRITICAL logs, and the launchd respawn contract all stay exactly as they are.

## Architectural Impact

- **New dependencies**: `import faulthandler` in `worker/__main__.py` (Python stdlib, no external dep). `signal` and `os` are already imported.
- **Interface changes**: none. `_self_kill()` keeps its signature (`() -> None`) and its "never returns" contract.
- **Coupling**: unchanged — still one seam, both guards call it.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — the change is localized to `_self_kill()`. Reverting the two-line body restores prior behavior.

## Appetite

**Size:** Small

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the SIGKILL-with-forensics-dump decision below is accepted)
- Review rounds: 1 (the signal choice and the test assertion changes)

This is a single-seam behavior change plus mechanical narrative updates across 3 tests and 3 docs. Coding time is minutes; the substance is the design decision (resolved below) and getting every stale SIGABRT mention consistent.

## Prerequisites

No prerequisites — this work has no external dependencies. `faulthandler` and `signal` are Python stdlib. No new secrets, services, or config.

## Solution

### Key Elements

- **`_self_kill()` seam**: emit an all-thread Python traceback for forensics, then deliver an uncatchable SIGKILL (no macOS crash dialog).
- **Storm-cap and dead-man's-switch call sites**: unchanged behavior; only their SIGABRT-narrative docstrings/comments are updated to say SIGKILL.
- **Three tests**: update signal assertions and the seam test to expect SIGKILL; leave the unrelated bridge-watchdog escalation-ladder tests alone.
- **Three docs**: replace the SIGABRT crash-report narrative with the SIGKILL + thread-dump narrative, including the #1808 forensics note.

### Flow

Guard fires (storm-cap or dead-man's-switch) → CRITICAL log → `_self_kill()` → `faulthandler.dump_traceback(all_threads=True)` to stderr (in `try:`) → `os.kill(getpid, SIGKILL)` (in `finally:`) → process dies uncatchably, no dialog → launchd respawns clean worker → operator reads the thread dump in `logs/worker_error.log`.

### Technical Approach

**The design decision (the SIGABRT-forensics-vs-SIGKILL-silence tradeoff).** The issue frames four open questions around whether the `.ips` forensic value justifies keeping SIGABRT (at least in production). The plan resolves them as follows:

**Decision: uniform SIGKILL in `_self_kill()`, plus an explicit `faulthandler` thread dump for forensics. No environment-gated signal fork.**

```python
import faulthandler  # add to worker/__main__.py imports

def _self_kill() -> None:
    """Hard-kill this process (uncatchable, signal-based) so launchd respawns it.

    Dumps all thread stacks to stderr first — a real production wedge then leaves
    forensic evidence in logs/worker_error.log (better than the macOS .ips C-frame
    report for a Python-level wedge; see #1808). Then delivers SIGKILL: equally
    unswallowable as the former SIGABRT, but produces NO macOS crash-report dialog
    and NO Python-*.ips file. Extracted as a seam so unit tests can assert the call
    without killing the test process.

    The dump is best-effort; the SIGKILL is in a `finally` so it fires even if the
    dump raises (e.g. stderr closed/monkeypatched) — otherwise, inside the storm-cap
    asyncio done-callback the exception would be swallowed and the guard would
    silently fail to recycle, the exact trap the subprocess test exists to catch.
    """
    try:
        faulthandler.dump_traceback(all_threads=True)
        sys.stderr.flush()
    finally:
        os.kill(os.getpid(), signal.SIGKILL)
```

Rationale, answering the issue's four open questions:

1. *Is the `.ips` forensic value worth keeping in production?* — **No, because we can do better.** A Python-process `.ips` is a C-level (interpreter) stack trace; for a **Python-level** wedge (a frozen coroutine / synchronously-blocked event loop, the #1808 scenario) it shows CPython C frames, not the Python line where the loop is stuck. `faulthandler.dump_traceback(all_threads=True)` emits every thread's **Python** stack — strictly better for the Python-level wedge (#1808), complementary to the C-frame `.ips` otherwise — and it runs from the surviving off-loop heartbeat thread precisely when the loop is frozen. So SIGABRT is not needed to preserve forensics; the dump gives better forensics for the wedge scenario that actually occurs.

2. *Is `worker.log` + wedge tooling already sufficient, making `.ips` redundant?* — **Yes**, and this plan makes it decisively so by routing the thread dump to stderr, which `com.valor.worker.plist` (`StandardErrorPath`) captures into `logs/worker_error.log`. The `.ips` becomes redundant.

3. *Can the crash dialog be suppressed at the OS/test-harness level instead?* — **Rejected.** Disabling the macOS crash reporter (unloading `ReportCrash` via `launchctl`, or `defaults write com.apple.CrashReporter`) mutates global system state, is macOS-version-fragile, doesn't travel to CI, and leaves SIGABRT (an abnormal-termination signal) as the production death mode for no benefit once the thread dump exists.

4. *Do the tests still assert the correct exit condition?* — **Yes, updated to SIGKILL.** `test_worker_supervisor.py`'s subprocess assertion moves from `-signal.SIGABRT` (-6) to `-signal.SIGKILL` (-9); the seam test moves from patching `os.abort` to patching `os.kill`, and additionally asserts the SIGKILL still fires when `faulthandler.dump_traceback` is patched to raise (the `finally` guarantee).

**Rejected alternative — environment-gated signal (SIGABRT in prod, SIGKILL under pytest).** This is the most tempting middle path but is rejected: (a) it forks production behavior from test behavior, so the tests would no longer exercise the real production death mode — the exact "log-only false pass" trap the current subprocess test was written to prevent; (b) it adds a `PYTEST_CURRENT_TEST`-style branch that silently drifts; (c) the `.ips` it would preserve in production is the low-value C-frame report, which the `faulthandler` dump supersedes. A single uniform signal with a superior forensic artifact is simpler and better for the actual wedge scenario.

**Windows handling — dropped as dead code.** `signal.SIGKILL` does not exist on Windows, but the worker is macOS/launchd-only with no Windows CI, so a win32 branch is untestable dead code. Per NO LEGACY CODE tolerance, `_self_kill()` calls `os.kill(os.getpid(), signal.SIGKILL)` unconditionally and the win32 `== 3` assertion is deleted from `test_worker_supervisor.py`. AC#4 (no `os.abort`/SIGABRT reference) is satisfied either way.

**Scope of edits in `worker/__main__.py`** (13 references: 9 `SIGABRT` + 4 `os.abort`): the `_self_kill()` body + `import faulthandler`, and the SIGABRT/`os.abort` narrative in docstrings and comments at the two call sites. All become SIGKILL narrative; the "never `sys.exit(1)` — SystemExit is swallowed in a done-callback" reasoning stays (still true and still important).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_self_kill()` wraps the dump in `try:` and the SIGKILL in `finally:` — the dump is best-effort; the kill is guaranteed even if `faulthandler.dump_traceback` or `sys.stderr.flush()` raises (e.g. stderr closed/monkeypatched). Without the `finally`, a dump exception inside the storm-cap asyncio done-callback would be swallowed by the event loop and the guard would silently fail to recycle — the exact trap the subprocess test exists to catch. `test_self_kill_sends_sigkill` must assert `os.kill` still fires with `faulthandler.dump_traceback` patched to raise. The subprocess test (`test_storm_cap_kills_process`) is the behavioral backstop: it asserts the process actually dies with SIGKILL.
- [ ] The `WORKER_DEADMAN_ENABLED=false` rollback path (`_heartbeat_cycle`) still logs-only and does NOT call `_self_kill()` — covered by existing `test_worker_deadman.py` disabled-path tests; verify they still pass unchanged.

### Empty/Invalid Input Handling
- [ ] `_self_kill()` takes no arguments — no empty/None input surface. No change.

### Error State Rendering
- [ ] The forensic thread dump is the user-visible failure artifact. Verify (in the subprocess test) that the child's stderr contains a thread-dump header (e.g. `Current thread` / `Thread 0x`) in addition to the correct SIGKILL return code, proving forensics are emitted before death.

## Test Impact

- [ ] `tests/unit/test_worker_supervisor.py::test_storm_cap_kills_process` — UPDATE: assertion `result.returncode == -signal.SIGKILL` (was `-signal.SIGABRT`); **DELETE the win32 `== 3` branch entirely** (dead code — worker is macOS/launchd-only, no Windows CI). Update module + function docstrings (lines 7, 211-219, 229) from SIGABRT/`os.abort` to SIGKILL. Assert the child's stderr contains a `faulthandler` thread-dump header.
- [ ] `tests/unit/test_worker_deadman.py::TestSelfKillSeam::test_self_kill_calls_os_abort` — REPLACE: rename to `test_self_kill_sends_sigkill`; patch `os.kill` (and `faulthandler.dump_traceback`) instead of `os.abort`; assert `os.kill` called once with `(os.getpid(), signal.SIGKILL)` and that the dump was invoked before it. Add a second assertion that `os.kill` **still fires when `faulthandler.dump_traceback` is patched to raise** (proves the `finally` guarantee). Update the class docstring and the module docstring line 13 ("`_self_kill()` delegates to `os.abort()`").
- [ ] `tests/unit/test_worker_deadman.py` stale-beacon tests (lines 129-179, 277-347) — no behavioral change (they patch `_self_kill` directly); verify they still pass. No edit expected beyond any incidental SIGABRT mention in comments.
- [ ] `tests/unit/test_worker_watchdog.py` — UPDATE (narrative only): docstrings at lines ~934, 997, 1001 that say "SIGABRT for launchd respawn" → "SIGKILL". The `test_frozen_loop_surviving_thread_self_kills` test (line 996) patches `_self_kill` and is signal-agnostic — no behavioral change.
- [ ] `tests/unit/test_worker_watchdog.py` bridge-watchdog escalation ladder (lines 734-759 `SIGTERM → SIGKILL → bootout`, and the `launchctl` return-code tests) — DO NOT TOUCH. This is the external bridge_watchdog kill mechanism, unrelated to the `_self_kill()` self-recycle seam.

## Rabbit Holes

- **Environment-gated dual-signal logic.** Tempting to "preserve `.ips` in prod." Do not — decided against above; it re-forks test vs prod behavior and adds drift surface for zero net forensic gain.
- **Disabling/parsing the macOS crash reporter.** Do not touch `ReportCrash`, `launchctl`, or `DiagnosticReports` cleanup. The signal change makes `.ips` files stop being generated at the source; retroactively deleting the 8 existing `.ips` files is optional operator hygiene, not part of this fix.
- **Refactoring the two guard call sites.** The storm-cap and dead-man's-switch logic is correct and out of scope. Only their SIGABRT-narrative strings change; the control flow does not.
- **`faulthandler.enable()` / SIGABRT-fault-handler wiring.** We are calling `dump_traceback()` explicitly at the seam; do not additionally register global fault handlers or a `faulthandler.register(signal)` hook — that is a separate observability concern.

## Risks

### Risk 1: A test still asserts the old signal somewhere and silently passes/fails
**Impact:** A stale `-signal.SIGABRT` assertion would fail after the change (loud, good), or a stale SIGABRT docstring would mislead future readers (AC#4 violation).
**Mitigation:** The Verification table greps `worker/__main__.py` and the three test files for `SIGABRT`/`os.abort` and asserts zero matches. The subprocess test asserting `-signal.SIGKILL` is the behavioral backstop.

### Risk 2: `faulthandler.dump_traceback` behaves unexpectedly when the event loop is frozen
**Impact:** If the dump blocked or raised, the kill might not fire on a real wedge.
**Mitigation:** `dump_traceback` is synchronous and runs on the surviving off-loop heartbeat thread (not the frozen loop); it writes to stderr and returns. The kill is in a `finally:` so it fires even if the dump raises. **Caveat:** this covers an asyncio-level (coroutine/event-loop) freeze, which is the #1815/#1808 failure mode. It does NOT cover a true C-extension GIL freeze (a C call that never releases the GIL) — that would prevent the heartbeat thread from ever reaching `_self_kill()`, so neither the dump nor the kill fires. That gap is unchanged from the SIGABRT baseline (SIGABRT could not fire either), so this change introduces no regression. The subprocess test proves the process still dies with SIGKILL after the dump on the paths that do reach the seam.

### Risk 3: SIGKILL bypasses atexit/finally cleanup that SIGABRT also bypassed
**Impact:** None new — SIGABRT already bypassed Python-level cleanup. SIGKILL has identical semantics here. launchd respawn contract is unchanged.
**Mitigation:** No mitigation needed; behavior is equivalent. Documented in the docs update.

## Race Conditions

No new race conditions. `_self_kill()` is terminal — after `os.kill(getpid, SIGKILL)` no further code runs. The dead-man's-switch already runs on a dedicated off-loop thread (#1767/#1815); the dump reads thread stacks at kill time, which is a snapshot with no ordering requirement. The storm-cap path runs inside an asyncio done-callback; the `sys.exit` swallowing hazard it was written to avoid is unchanged (we still use a signal, not `sys.exit`).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1808] Deeper wedged-worker root-cause analysis and any additional forensic tooling beyond the `faulthandler` thread dump belong to the #1808 investigation, not this fix.
- Retroactive deletion of the 8 existing `Python-*.ips` files under `~/Library/Logs/DiagnosticReports/` — optional operator hygiene, not required for the fix (the change stops new ones at the source).

## Update System

No update system changes required — this is a purely internal behavior change to `worker/__main__.py`. No new dependencies (`faulthandler` is stdlib), no config, no `scripts/update/` or `migrations.py` changes. The next `/update` + `worker-restart` picks up the new kill behavior with no migration step.

## Agent Integration

No agent integration required — this is a worker-internal change to the self-recycle seam. No CLI entry point, no MCP server, no `.mcp.json` change, and the bridge does not import `_self_kill`. The behavior is exercised only by launchd (production respawn) and the three unit tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/worker-fault-containment.md` (2 SIGABRT/`os.abort` refs) — storm-cap now recycles via SIGKILL after emitting a `faulthandler` thread dump; no macOS crash dialog.
- [ ] Update `docs/features/worker-liveness-recovery.md` (5 refs) — dead-man's-switch now recycles via SIGKILL with a thread dump; add the #1808 note that forensics moved from the `.ips` C-frame report to the Python all-thread dump in `logs/worker_error.log` (stderr sink per the plist `StandardErrorPath`).
- [ ] Update `docs/features/worker-service.md` (1 ref) — general recycle narrative SIGABRT → SIGKILL.
- [ ] No `docs/features/README.md` index entry needed (these are existing docs, not new features).

### Inline Documentation
- [ ] `_self_kill()` docstring rewritten to describe the dump-then-SIGKILL contract and the #1808 forensic rationale.
- [ ] Storm-cap and dead-man's-switch inline comments/docstrings updated from SIGABRT to SIGKILL narrative (the `sys.exit`-swallowing note stays — still valid).

## Success Criteria

- [ ] A full test-suite run (manual + nightly) produces **zero** new macOS crash-report dialogs and **zero** new `Python-*.ips` files.
- [ ] The three tests still prove the recycle is an unswallowable, signal-based hard kill launchd would respawn, with assertions matching SIGKILL.
- [ ] The production-vs-test forensics tradeoff is decided (uniform SIGKILL + `faulthandler` dump) and documented in the three docs and the `_self_kill()` docstring.
- [ ] No stale SIGABRT/`os.abort` narrative remains in `worker/__main__.py`, the 3 test files, or the 3 docs.
- [ ] `_self_kill()` emits an all-thread Python traceback to stderr before killing (verified in the subprocess test).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (worker-seam)**
  - Name: `seam-builder`
  - Role: Change `_self_kill()` to dump-then-SIGKILL, add `import faulthandler`, update all SIGABRT narrative in `worker/__main__.py`, and update the three test files' assertions/docstrings.
  - Agent Type: builder
  - Domain: async/concurrency (signal delivery, off-loop thread, asyncio done-callback)
  - Resume: true

- **Documentarian (worker-docs)**
  - Name: `worker-doc`
  - Role: Update the three feature docs' SIGABRT narrative to SIGKILL + thread-dump, including the #1808 forensics note.
  - Agent Type: documentarian
  - Resume: true

- **Validator (recycle)**
  - Name: `recycle-validator`
  - Role: Verify no stale SIGABRT/`os.abort` references, run the three worker tests, confirm the subprocess test asserts SIGKILL and thread-dump output.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Change the kill seam and worker narrative
- **Task ID**: build-seam
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_deadman.py`, `tests/unit/test_worker_supervisor.py`, `tests/unit/test_worker_watchdog.py`
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `import faulthandler` to `worker/__main__.py` imports.
- Rewrite `_self_kill()` body: `try:` block does `faulthandler.dump_traceback(all_threads=True)` → `sys.stderr.flush()`; `finally:` block does `os.kill(os.getpid(), signal.SIGKILL)` (no win32 branch). Rewrite its docstring per Technical Approach.
- Update every SIGABRT/`os.abort` mention in docstrings and comments (13 refs: 9 `SIGABRT` + 4 `os.abort`, at the two call sites) to SIGKILL narrative; keep the "never `sys.exit(1)` in a done-callback" reasoning.

### 2. Update the three test files
- **Task ID**: build-tests
- **Depends On**: build-seam
- **Validates**: the three worker test files pass
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- `test_worker_supervisor.py`: assertion → `-signal.SIGKILL`; **delete the win32 `== 3` branch**; docstrings SIGABRT → SIGKILL; add an assertion that child stderr contains a thread-dump header.
- `test_worker_deadman.py`: rename `test_self_kill_calls_os_abort` → `test_self_kill_sends_sigkill`, patch `os.kill` + `faulthandler.dump_traceback`, assert SIGKILL to own pid and dump-before-kill, **plus assert SIGKILL still fires when `faulthandler.dump_traceback` raises** (the `finally` guarantee); fix class + module docstrings.
- `test_worker_watchdog.py`: docstring SIGABRT → SIGKILL at the `_self_kill`-related lines only. DO NOT touch the bridge-watchdog escalation-ladder tests.

### 3. Update the three docs
- **Task ID**: document-recycle
- **Depends On**: build-seam
- **Assigned To**: worker-doc
- **Agent Type**: documentarian
- **Parallel**: true
- Rewrite SIGABRT narrative to SIGKILL + `faulthandler` dump in `worker-fault-containment.md`, `worker-liveness-recovery.md` (with #1808 forensics note), `worker-service.md`.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-recycle
- **Assigned To**: recycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the three worker tests; confirm SIGKILL assertions and thread-dump output.
- Grep for stale `SIGABRT`/`os.abort` in `worker/__main__.py`, the 3 tests, and the 3 docs — expect zero.
- Run `ruff check`/`ruff format --check`.
- Report pass/fail against Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Worker recycle tests pass | `pytest tests/unit/test_worker_supervisor.py tests/unit/test_worker_deadman.py tests/unit/test_worker_watchdog.py -q` | exit code 0 |
| SIGKILL is the kill signal | `grep -c 'signal.SIGKILL' worker/__main__.py` | output > 0 |
| No SIGABRT in worker module | `grep -c 'SIGABRT' worker/__main__.py` | match count == 0 |
| No os.abort in worker module | `grep -c 'os.abort' worker/__main__.py` | match count == 0 |
| No SIGABRT in worker tests | `grep -rc 'SIGABRT\|os.abort' tests/unit/test_worker_supervisor.py tests/unit/test_worker_deadman.py tests/unit/test_worker_watchdog.py` | match count == 0 |
| No SIGABRT in worker docs | `grep -rc 'SIGABRT\|os.abort' docs/features/worker-fault-containment.md docs/features/worker-liveness-recovery.md docs/features/worker-service.md` | match count == 0 |
| faulthandler dump wired | `grep -c 'faulthandler.dump_traceback' worker/__main__.py` | output > 0 |
| Forensic sink is worker_error.log | `plutil -extract StandardErrorPath raw ~/Library/LaunchAgents/com.valor.worker.plist` (or `grep -A1 StandardErrorPath` the plist) | path ends in `logs/worker_error.log` |
| SIGKILL guaranteed on dump failure | `grep -A6 'def _self_kill' worker/__main__.py` shows `os.kill` under a `finally:` | `finally:` present |
| Lint clean | `python -m ruff check worker/ tests/unit/test_worker_supervisor.py tests/unit/test_worker_deadman.py tests/unit/test_worker_watchdog.py` | exit code 0 |
| Format clean | `python -m ruff format --check worker/__main__.py` | exit code 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — FULL depth (3 critics). 0 blockers, 3 concerns, 4 nits. All embedded in this revision:

- **Concern 1 (kill-not-guaranteed):** SIGKILL moved into a `finally:` so it fires even if the dump raises; `test_self_kill_sends_sigkill` must assert this. → Technical Approach, Failure Path Test Strategy, Test Impact.
- **Concern 2 (wrong forensic sink):** all references corrected from `logs/worker.log` to `logs/worker_error.log` (`faulthandler` → stderr → plist `StandardErrorPath`); plist-verification row added. → Data Flow, Flow, Technical Approach, Documentation, Verification.
- **Concern 3 (open-questions contradiction):** OQs relabeled as confirmed decisions below; body and tail now agree.
- **Nits:** GIL-starvation caveat scoped (Risk 2); "~15 references" corrected to 13; "strictly more diagnostic" softened to "strictly better for the Python-level wedge (#1808), complementary otherwise".

## Resolved Decisions

1. **Forensics mechanism — DECIDED: uniform SIGKILL + `faulthandler.dump_traceback(all_threads=True)` to stderr (captured in `logs/worker_error.log`).** The Python all-thread dump beats the `.ips` C-frame report for the #1808 Python-level wedge and runs from the surviving off-loop heartbeat thread. The env-gated SIGABRT-in-prod alternative is rejected (re-forks test vs prod behavior; drift surface; the `.ips` it preserves is the low-value C-frame report). No open question remains.
2. **Windows handling — DECIDED: dropped as dead code.** The worker is macOS/launchd-only with no Windows CI. `_self_kill()` calls `os.kill(os.getpid(), signal.SIGKILL)` unconditionally; the win32 `os._exit(3)` branch and the test's win32 `== 3` assertion are deleted (NO LEGACY CODE). No open question remains.
