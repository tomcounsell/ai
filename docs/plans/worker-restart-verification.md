---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/999
last_comment_id:
revision_applied: true
---

# Worker Restart Verification + Resume Hydration Field Fix

## Problem

Two silent failures leave the system unresponsive after a routine `/update` run.

**Current behavior:**

1. **Worker stays stopped after update.** The update orchestrator kills the worker via SIGTERM, calls `install_worker()` (which does `launchctl bootout` → `launchctl bootstrap`), then polls 30 seconds for a heartbeat file. If the worker hasn't started by then — possible due to launchd ThrottleInterval or slow startup — the orchestrator logs `WARN: Worker not running after install` and exits successfully. The worker remains stopped. No Telegram replies are delivered. PM sessions with completed children stay stuck in `waiting_for_children` indefinitely.

2. **Resume hydration silently no-ops.** `_maybe_inject_resume_hydration()` sets `chosen.message_text = hydration_block` (which correctly updates the underlying `initial_telegram_message` dict via the property setter), then calls `await chosen.async_save(update_fields=["message_text", "updated_at"])`. Popoto does not recognize `message_text` as a real field — it emits a warning and skips the save entirely. The `<resumed-session-context>` block is never persisted; resumed sessions see no hydration context on their first turn.

**Desired outcome:**

1. After the 30-second window expires with no running process, the orchestrator issues a `launchctl kickstart` to force-start the service and polls for an additional 15 seconds. If the worker is still not running after the full 45-second window, the update exits with a non-zero code and a clear error message so the operator knows the system is degraded.

2. `async_save` in the hydration path uses `update_fields=["initial_telegram_message", "updated_at"]` — the real Popoto field name. The hydration context is saved correctly and resumed sessions see the context block on their first turn.

## Freshness Check

**Baseline commit:** `229a8af8`
**Issue filed at:** 2026-04-16T06:50:00Z (estimated — same session as this plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/run.py:774–812` — worker verification loop: 15 × 2s poll, no kickstart fallback, warning-only — confirmed at HEAD
- `scripts/update/service.py:196–225` — `install_worker()` returns bool, no wait — confirmed at HEAD
- `agent/agent_session_queue.py:619–681` — `_maybe_inject_resume_hydration()` calls `async_save(update_fields=["message_text", ...])` — confirmed at HEAD (line 669)
- `models/agent_session.py:158` — `initial_telegram_message = DictField(null=True)` is the real field — confirmed at HEAD
- `models/agent_session.py:661–668` — `message_text` setter correctly updates `initial_telegram_message` dict — confirmed at HEAD

**Cited sibling issues/PRs re-checked:**
- #776 (Worker launchd restart takes ~9 minutes) — closed 2026-04-07; fix (PR #789) made worker exit with code 1 on SIGTERM. Still relevant: ThrottleInterval now applies correctly, but the 30-second update window can still expire before the new bootstrap starts the process.
- #755 (Worker service gaps) — closed 2026-04-07; covered uninstall/restart gaps but not the update-orchestrator heartbeat verification gap.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:**
- `worker_lifecycle_fixes.md` (#984, status: Planning) — covers stale restart flag TTL and zombie PID status checks. Adjacent domain but different bugs; no file-level conflict with the two changes here.

**Notes:** `resume-hydration-context.md` (status: docs_complete) introduced the hydration feature; the field-name bug is a regression in that implementation.

## Prior Art

- **Issue #776 / PR #789** — Worker launchd restart takes ~9 minutes. Root cause: clean exit (code 0) caused launchd to apply the default ~10-minute throttle instead of `ThrottleInterval`. Fix: exit with code 1 on SIGTERM. Relevant because it established the ThrottleInterval behavior we rely on; after that fix the 30-second window *should* be sufficient in the normal case, but the update's `bootout`+`bootstrap` sequence bypasses launchd's normal restart path entirely and can still take longer than 30s.
- **Issue #755 / PR #737** — Worker service gaps. Fixed uninstall/restart issues but did not add kickstart fallback to the update orchestrator's heartbeat loop.

## Research

No relevant external findings — both fixes are pure internal code changes with no external library involvement.

## Data Flow

### Fix 1: Worker restart path

1. **Entry:** `/update` (`scripts/update/run.py --full`) reaches the worker install step (~line 774)
2. **`install_worker()`** (`scripts/update/service.py:196–225`): calls `launchctl bootout` (unregisters existing service) then `launchctl bootstrap` (registers and starts fresh)
3. **30s poll** (`run.py:786–812`): checks `service.is_worker_running()` and heartbeat file mtime every 2s
4. **If poll succeeds:** logs `Worker running (PID: N)`, continues
5. **If poll fails, process exists but no heartbeat:** warns `Worker started but heartbeat pending`
6. **If poll fails, no process (current bug):** warns `Worker not running after install`, continues — **this is wrong**
7. **Fix adds:** `launchctl kickstart -k "gui/{uid}/com.valor.worker"`, 15-second re-poll; if still dead → `result.success = False`, error log

### Fix 2: Resume hydration save path

1. **Entry:** Worker picks up a PM session in `_pop_agent_session()` (`agent_session_queue.py`)
2. **`_maybe_inject_resume_hydration(chosen, worker_key)`:** detects 2+ `*_resume.json` files in session log dir
3. **Sets `chosen.message_text = hydration_block + original`** — property setter correctly writes into `chosen.initial_telegram_message["message_text"]`
4. **`await chosen.async_save(update_fields=["message_text", ...])`** — **bug:** Popoto skips `message_text` (not a registered field), issues warning, save is a no-op
5. **Fix:** change to `update_fields=["initial_telegram_message", "updated_at"]` — Popoto saves the DictField containing the updated text

## Architectural Impact

- **No new dependencies.** Both fixes are targeted one-liners.
- **Interface changes:** `install_worker()` behavior is unchanged; the kickstart logic lives in `run.py`'s caller block. The `_maybe_inject_resume_hydration` signature is unchanged.
- **Coupling:** No change.
- **Reversibility:** Trivially reversible — revert two lines.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Kickstart fallback** (`scripts/update/run.py:804–812`): After the 30-second heartbeat window expires with `worker_pid == None`, run `subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.valor.worker"])` wrapped in `try/except Exception` (matching existing error-handling pattern in this function) and poll for 15 more seconds using the same heartbeat check. If still not running, set `result.success = False` and log an error. Use the module-level `os` import (line 15) — no inline `import os as _os`.
- **Field name fix** (`agent/agent_session_queue.py:669`): Change `update_fields=["message_text", "updated_at"]` → `update_fields=["initial_telegram_message", "updated_at"]`.
- **Test assertion** (`tests/unit/test_resume_hydration.py`): Add assertion to `test_two_resume_files_triggers_hydration` that `async_save` was called with `update_fields=["initial_telegram_message", "updated_at"]`.

### Flow

`/update` runs → install_worker() → 30s poll → **no process** → `launchctl kickstart` → 15s re-poll → **worker alive** → continue  
OR → **still no process** → error exit (non-zero)

### Technical Approach

**Fix 1** — in `run.py`, after the `if not worker_healthy:` / `if worker_pid:` else branch (the "no process" path):

```python
# Kickstart fallback: force-start the service if launchd didn't auto-start
# NOTE: os is already imported at module level (line 15) — no inline import needed.
# The subprocess.run call is wrapped in try/except to match the existing error-handling
# pattern in this function (see OSError catch at line ~796). If launchctl is missing or
# fails with an OS error, we log and fall through to the re-poll — the worker may have
# started another way.
uid = os.getuid()
try:
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/com.valor.worker"],
        capture_output=True,
    )
except Exception as e:
    log(f"launchctl kickstart failed: {e}", v, always=True)
# Re-poll for 15 more seconds
for _ in range(8):
    _time.sleep(2)
    if service.is_worker_running():
        worker_pid = service.get_worker_pid()
        if heartbeat_file.exists() and heartbeat_file.stat().st_mtime > install_ts:
            log(f"Worker running after kickstart (PID: {worker_pid})", v, always=True)
            worker_healthy = True
            break
if not worker_healthy:
    log("ERROR: Worker not running after kickstart retry — system degraded", v, always=True)
    result.warnings.append("Worker not running after install and kickstart retry")
    result.success = False
```

**Fix 2** — in `agent/agent_session_queue.py` line ~669:

```python
# Before (broken):
await chosen.async_save(update_fields=["message_text", "updated_at"])

# After (correct):
await chosen.async_save(update_fields=["initial_telegram_message", "updated_at"])
```

**Test fix** — in `test_two_resume_files_triggers_hydration`, add:

```python
session.async_save.assert_called_once_with(
    update_fields=["initial_telegram_message", "updated_at"]
)
```

## Failure Path Test Strategy

### Exception Handling Coverage
- The kickstart fallback in `run.py` wraps `subprocess.run` in `try/except Exception` — failure to run `launchctl` (e.g., binary not found, permission denied) is caught, logged via `log(f"launchctl kickstart failed: {e}", v, always=True)`, and execution falls through to the re-poll loop. This matches the existing `OSError` catch pattern at ~line 796.
- `_maybe_inject_resume_hydration` already has a top-level `except Exception` that logs and returns — confirmed at `agent_session_queue.py:673–677`.

### Empty/Invalid Input Handling
- `launchctl kickstart` with an invalid service label exits non-zero; `subprocess.run` captures that — treat non-zero return as "kickstart failed" and still re-poll (the service might have started another way).

### Error State Rendering
- The `result.success = False` path in `run.py` will cause the orchestrator's final output to show `FAILED` rather than `COMPLETED with N warning(s)`, which is the correct operator-visible signal.

## Test Impact

- [x] `tests/unit/test_resume_hydration.py::TestMaybeInjectResumeHydration::test_two_resume_files_triggers_hydration` — UPDATE: add `assert_called_once_with(update_fields=["initial_telegram_message", "updated_at"])` to verify the correct field is saved
- [x] `tests/unit/test_resume_hydration.py::TestMaybeInjectResumeHydration::test_hydration_prepends_before_original` — UPDATE: same assertion on `async_save` call args
- [x] `tests/unit/test_resume_hydration.py::TestMaybeInjectResumeHydration::test_three_resume_files_triggers_hydration` — UPDATE: same assertion

No integration tests for `scripts/update/run.py` worker-start verification exist — this is greenfield coverage.

## Rabbit Holes

- **Rewriting `install_worker.sh`** — the shell script is fine; the gap is in the Python orchestrator's post-install check, not the install itself.
- **Making the update orchestrator retry the full `install_worker()` again** — `bootout`+`bootstrap` already ran; the service is registered. A `kickstart` is the right tool to trigger a start without re-registering.
- **Adjusting `ThrottleInterval` in the plist** — PR #789 already addressed the throttle issue. The kickstart bypasses throttle entirely, which is what we want.
- **Fixing the `worker-status` heartbeat check** — that's tracked separately in `worker_lifecycle_fixes.md` (#984).

## Risks

### Risk 1: `launchctl kickstart` fails silently on some macOS versions
**Impact:** Kickstart no-ops, worker still not running, but re-poll shows no process — orchestrator correctly exits with error.
**Mitigation:** We check `is_worker_running()` after kickstart regardless of its exit code; the error path fires correctly either way.

### Risk 2: `result.success = False` breaks callers that treat warnings-only as success
**Impact:** Scripts wrapping `run.py --full` that check exit code would now fail on worker-start failure. This is the *desired* behavior — degraded state should be visible.
**Mitigation:** Acceptable regression; an orchestrator that lies about system health is worse than one that fails loudly.

## Race Conditions

### Race 1: Kickstart fires while worker is already starting
**Location:** `scripts/update/run.py`, kickstart block
**Trigger:** launchd was already in the process of starting the worker when kickstart fires
**Mitigation:** `launchctl kickstart -k` (the `-k` flag kills any existing instance first) is idempotent — if the worker is already running, it restarts it. The re-poll then detects the fresh heartbeat. Acceptable: a double-start is safer than a missing worker.

No race conditions in Fix 2 — `async_save` is a single async call on a single session object with no concurrent writers.

## No-Gos (Out of Scope)

- Increasing the initial 30-second poll window — the kickstart fallback is the right lever; extending the wait only delays failure detection.
- Fixing the `worker-status` zombie PID issue — tracked in #984.
- Adding a worker health check to the bridge watchdog — separate concern.
- Surfacing the degraded state as a Telegram alert — separate concern; the error log is sufficient for now.

## Update System

The kickstart fallback lives in `scripts/update/run.py`, which IS the update system. No separate propagation needed — the fix ships with the next pull on all machines.

## Agent Integration

No agent integration required — both changes are in the update orchestrator and worker startup path, not in any MCP-exposed tool.

## Documentation

- [x] Update `docs/features/bridge-worker-architecture.md` to note that `/update` now retries worker start via `launchctl kickstart` if the 30-second heartbeat window expires.
- [x] No new feature doc needed — this is a bug fix to existing infrastructure.

## Success Criteria

- [x] Running `/update` with a simulated slow-start worker triggers `launchctl kickstart` in the fallback path (visible in update output)
- [x] If worker is still dead after kickstart retry, update exits with non-zero code and `ERROR:` line in output
- [x] The "Unknown field 'message_text' in update_fields" warning no longer appears in worker logs during resume hydration
- [x] `test_two_resume_files_triggers_hydration` asserts `update_fields=["initial_telegram_message", "updated_at"]` and passes
- [x] `pytest tests/unit/test_resume_hydration.py` passes
- [x] `pytest tests/ -x -q` passes
- [x] Resume hydration persistence: `async_save` with `update_fields=["initial_telegram_message", "updated_at"]` correctly persists the hydration block (verified by unit test mock assertions; full end-to-end persistence validation is out of scope for this Small fix but should be covered by a future integration test)
- [x] `python -m ruff check .` exits 0

## Team Orchestration

### Team Members

- **Builder (fixes)**
  - Name: fixes-builder
  - Role: Implement kickstart fallback in run.py and fix field name in agent_session_queue.py; update test assertions
  - Agent Type: builder
  - Resume: true

- **Validator (fixes)**
  - Name: fixes-validator
  - Role: Verify both code changes are correct, tests updated and passing, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update bridge-worker-architecture.md with kickstart behavior
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Implement both fixes
- **Task ID**: build-fixes
- **Depends On**: none
- **Validates**: `tests/unit/test_resume_hydration.py`, `python -m ruff check .`
- **Assigned To**: fixes-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/update/run.py` (~line 809), after the `else:` branch that logs "WARN: Worker not running after install", add `launchctl kickstart -k` call wrapped in `try/except Exception` (log error on failure, fall through to re-poll) and 15-second re-poll; set `result.success = False` if still dead. Use module-level `os` import, not inline `import os as _os`.
- In `agent/agent_session_queue.py` line 669, change `update_fields=["message_text", "updated_at"]` to `update_fields=["initial_telegram_message", "updated_at"]`
- In `tests/unit/test_resume_hydration.py`, update the three `async_save.assert_called_once()` calls to assert `update_fields=["initial_telegram_message", "updated_at"]`

### 2. Validate fixes
- **Task ID**: validate-fixes
- **Depends On**: build-fixes
- **Assigned To**: fixes-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_resume_hydration.py -v` — all tests pass
- Run `pytest tests/ -x -q` — full suite passes
- Run `python -m ruff check . && python -m ruff format --check .` — clean
- Confirm `update_fields=["message_text"` no longer appears in `agent_session_queue.py`
- Confirm kickstart fallback exists in `scripts/update/run.py`

### 3. Documentation + Final Validation
- **Task ID**: document-and-validate
- **Depends On**: validate-fixes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md`: add note that update orchestrator retries worker start via `launchctl kickstart` if 30-second heartbeat window expires; note error exit on persistent failure
- Verify documentation updated, then run full test suite (`pytest tests/ -x -q`) and lint (`python -m ruff check . && python -m ruff format --check .`) to confirm no regressions
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Resume hydration tests pass | `pytest tests/unit/test_resume_hydration.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Wrong field name gone | `grep -n '"message_text"' agent/agent_session_queue.py` | output contains 0 occurrences in update_fields context |
| Kickstart fallback present | `grep -n 'kickstart' scripts/update/run.py` | output contains match |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | Kickstart subprocess.run missing try/except | Task build-fixes | Wrapped in try/except Exception, matching existing OSError pattern in run.py; failure logs and falls through to re-poll |
| NIT | Adversary | Inline `import os as _os` is unnecessary | Task build-fixes | Use module-level `os` import (line 15); removed inline alias |
| NIT | Simplifier | Task 4 duplicates Task 2 | Plan revision | Merged Task 4 into Task 3 (document-and-validate) |
| NIT | User | No end-to-end success criterion for resume hydration | Plan revision | Added success criterion for persistence verification; full e2e deferred to future integration test |

---

## Open Questions

None — both fixes are confirmed by code inspection. Ready for build.
