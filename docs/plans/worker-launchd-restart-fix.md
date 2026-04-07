---
status: docs_complete
type: bug
appetite: Small
owner: valorengels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/776
last_comment_id:
---

# Worker launchd Restart Fix

## Problem

Worker restarts take ~9 minutes instead of the configured 10-second `ThrottleInterval`. When the worker is killed via SIGTERM (e.g., `./scripts/valor-service.sh worker-restart`), it does not come back online until nearly 10 minutes later. During that window the system cannot process any `AgentSession` records — Telegram messages go unanswered.

**Current behavior:**
- Worker exits with code 0 after SIGTERM cleanup
- launchd treats code 0 as voluntary success and applies its internal ~10-minute default throttle
- `ThrottleInterval = 10` in `com.valor.worker.plist` is ignored for code-0 exits
- Additionally, `stop_worker()` in `scripts/valor-service.sh` calls `launchctl unload`, which removes the worker from launchd supervision entirely — `KeepAlive` is destroyed until the user manually re-bootstraps

**Desired outcome:**
- Worker killed via SIGTERM restarts within 15 seconds (ThrottleInterval=10s + margin)
- `valor-service.sh worker-restart` completes and worker is running within 15 seconds
- `stop_worker()` uses `launchctl bootout` (modern API) for consistent behavior with `install_worker.sh`

## Prior Art

- **PR #742**: "Worker persistent mode and graceful shutdown" — Introduced the SIGTERM handler in `worker/__main__.py` with graceful cleanup. This is where exit code 0 behavior originated. The PR correctly implemented graceful shutdown but did not account for the launchd exit-code behavior.
- **PR #737**: "Extract standalone worker service from bridge monolith" — Created the worker as a standalone launchd service. `install_worker.sh` correctly used `bootout`/`bootstrap`, but `valor-service.sh` was updated with the deprecated `launchctl unload` instead.
- **Issue #755**: "Worker service gaps" — Identified the ~9 minute restart gap as one of five operational gaps. The fix in #755 addressed the other four gaps but did not tackle the SIGTERM exit code or the `launchctl unload` issue.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #742 | Added graceful SIGTERM handler | Did not exit with non-zero code — launchd treated SIGTERM shutdown as voluntary success |
| Issue #755 | Fixed 4 of 5 worker service gaps | The restart gap was listed but the fix was scoped out — root cause (exit code + launchctl unload) was left for a follow-up |

**Root cause pattern:** Both issues resulted from incomplete understanding of launchd's exit-code semantics. `ThrottleInterval` only applies to crash restarts (non-zero exit), not voluntary exits (code 0).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this is a pure code and shell script fix with no external dependencies.

## Solution

### Key Elements

- **Worker exit code**: Change `worker/__main__.py` to exit with code 1 (not 0) when shutdown is triggered by SIGTERM, signaling to launchd that termination was external/forced
- **launchctl modernization**: Replace `launchctl unload "$WORKER_PLIST_PATH"` with `launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME"` in `stop_worker()` so supervision is maintained through stop/start cycles
- **SIGINT distinction**: SIGINT (Ctrl-C, developer) should still exit 0 — it's a voluntary developer action, not an external termination event

### Flow

SIGTERM received → graceful cleanup runs → `sys.exit(1)` → launchd sees non-zero → `ThrottleInterval` (10s) applies → worker restarts within 15 seconds

`valor-service.sh worker-stop` → `launchctl bootout` (service removed from supervision but plist still installed) → `valor-service.sh worker-start` → `launchctl bootstrap` re-registers → worker running

### Technical Approach

**Fix 1 — `worker/__main__.py`:**

Introduce a module-level flag `_shutdown_via_signal = False`. The SIGTERM handler sets this flag before setting `shutdown_event`. In `main()`, after `asyncio.run(_run_worker(...))` returns, check the flag and call `sys.exit(1)` if SIGTERM was the cause. SIGINT leaves the flag unset and exits 0 (voluntary developer stop).

```python
_shutdown_via_signal = False

def _signal_handler(sig, frame):
    global _shutdown_via_signal
    logger.info(f"Received signal {sig}, shutting down gracefully...")
    if sig == signal.SIGTERM:
        _shutdown_via_signal = True
    request_shutdown()
    shutdown_event.set()
```

After `asyncio.run(...)`:
```python
if _shutdown_via_signal:
    logger.info("Exiting with code 1 (SIGTERM) so launchd respects ThrottleInterval")
    sys.exit(1)
```

**Fix 2 — `scripts/valor-service.sh`:**

In `stop_worker()`, replace:
```bash
launchctl unload "$WORKER_PLIST_PATH" 2>/dev/null || true
```
with:
```bash
launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME" 2>/dev/null || true
```

This matches the API used in `scripts/install_worker.sh` (line 49) and properly removes the service from the domain without corrupting the plist registration state.

## Failure Path Test Strategy

### Exception Handling Coverage

- No exception handlers are modified in this change
- The SIGTERM handler is a bare synchronous function — no try/except blocks exist or are needed

### Empty/Invalid Input Handling

- Signal handler receives `sig` and `frame` from the OS — these cannot be empty or invalid
- `launchctl bootout` silently ignores missing services (via `|| true`) — safe for when worker is not loaded

### Error State Rendering

- If `sys.exit(1)` is called, the worker process terminates — launchd handles restart
- Worker logs will contain "Exiting with code 1 (SIGTERM)" confirming the exit path

## Test Impact

No existing tests affected — the worker signal handling and launchd interaction are infrastructure-level behaviors that are not covered by the existing unit or integration test suite. The worker startup/shutdown tests in `tests/unit/` test the `_run_worker` logic directly without simulating launchd or OS signals at the process level.

New tests to add:
- [x] `tests/unit/test_worker_main.py` — CREATE: unit test that sends SIGTERM to the signal handler and asserts `_shutdown_via_signal` is set to True (implemented in `tests/unit/test_worker_entry.py::TestSigtermExitCode`)

## Race Conditions

No race conditions identified. The signal handler sets a module-level flag synchronously before `asyncio.run()` returns. The flag is read only after the async cleanup completes in `main()`, so there is no concurrent read/write hazard.

## No-Gos (Out of Scope)

- Changing `ThrottleInterval` value in the plist — 10 seconds is the correct configured value
- Fixing `launchctl unload` calls for bridge, update, or watchdog services — those are separate services with different lifecycle requirements; scope is worker only
- Adding health-check retry logic or exponential backoff — restart timing is launchd's responsibility
- Windows or Linux service management — macOS launchd only

## Update System

The `scripts/remote-update.sh` and `/update` skill deploy code changes by pulling the latest git HEAD and restarting services. This fix modifies `worker/__main__.py` and `scripts/valor-service.sh` — both are part of the normal git pull. No additional update script changes are needed.

Existing installations will automatically get the fix on next `./scripts/valor-service.sh restart` after pulling.

## Agent Integration

No agent integration required — this is a launchd service lifecycle fix internal to the worker process and service management script. No MCP servers, `.mcp.json` changes, or bridge modifications needed.

## Documentation

- [x] Update `docs/features/bridge-worker-architecture.md` to note that the worker exits with code 1 on SIGTERM so launchd respects `ThrottleInterval`
- [x] No new feature doc needed — this is a bug fix with no new user-facing capability

## Success Criteria

- [x] Worker killed via SIGTERM restarts within 15 seconds
- [x] `valor-service.sh worker-restart` completes and worker is confirmed running within 15 seconds
- [x] `stop_worker()` uses `launchctl bootout` (confirmed via `grep`)
- [x] Worker logs show "Exiting with code 1 (SIGTERM)" on termination
- [x] Unit test for `_shutdown_via_signal` flag passes
- [x] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (worker-fix)**
  - Name: worker-fix-builder
  - Role: Apply both fixes — SIGTERM exit code in `worker/__main__.py` and `launchctl bootout` in `scripts/valor-service.sh`
  - Agent Type: builder
  - Resume: true

- **Validator (worker-fix)**
  - Name: worker-fix-validator
  - Role: Verify fixes are applied correctly, run unit tests, confirm grep checks pass
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update `docs/features/bridge-worker-architecture.md` with SIGTERM exit code note
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

builder, validator, documentarian

## Step by Step Tasks

### 1. Apply SIGTERM exit code fix
- **Task ID**: build-sigterm-exit
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_main.py` (create)
- **Assigned To**: worker-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `worker/__main__.py`, introduce `_shutdown_via_signal` module-level flag
- Update `_signal_handler` to set flag on SIGTERM only
- After `asyncio.run(_run_worker(...))` in `main()`, call `sys.exit(1)` if flag is set
- Add log line "Exiting with code 1 (SIGTERM) so launchd respects ThrottleInterval"
- Create `tests/unit/test_worker_main.py` with a test that invokes `_signal_handler` with `signal.SIGTERM` and asserts `_shutdown_via_signal` is True

### 2. Apply launchctl bootout fix
- **Task ID**: build-launchctl-fix
- **Depends On**: none
- **Validates**: `grep -n "bootout" scripts/valor-service.sh | grep WORKER`
- **Assigned To**: worker-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/valor-service.sh`, replace `launchctl unload "$WORKER_PLIST_PATH"` with `launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME"` in `stop_worker()` (line 551)
- Verify no other worker-specific `launchctl unload` calls remain (line 475 is in `uninstall_service()` which removes the plist entirely — that case may still use unload or can use bootout+rm)

### 3. Validate fixes
- **Task ID**: validate-fixes
- **Depends On**: build-sigterm-exit, build-launchctl-fix
- **Assigned To**: worker-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_main.py -v` and confirm pass
- Run `grep -n "bootout" scripts/valor-service.sh` and confirm worker stop uses bootout
- Run `python -m ruff check worker/__main__.py scripts/valor-service.sh 2>/dev/null || true`
- Confirm `_shutdown_via_signal` flag is set only on SIGTERM, not SIGINT

### 4. Documentation
- **Task ID**: document-fix
- **Depends On**: validate-fixes
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Add note to `docs/features/bridge-worker-architecture.md` explaining SIGTERM exit code behavior and why it matters for launchd `ThrottleInterval`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-fix
- **Assigned To**: worker-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` and confirm all pass
- Run `python -m ruff check . && python -m ruff format --check .`
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| bootout in stop_worker | `grep -n "bootout" /Users/valorengels/src/ai/scripts/valor-service.sh` | output contains `WORKER_PLIST_NAME` |
| exit flag in worker | `grep -n "_shutdown_via_signal" /Users/valorengels/src/ai/worker/__main__.py` | output contains `sys.exit` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — root causes are confirmed and fixes are well-defined.
