---
status: Planning
type: bug
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/55
---

# Update Health Check Retry (Fix False Negative)

## Problem

`scripts/update/run.py --full` reports `WARN: Bridge not running after restart` even when the bridge starts successfully. The health check polls 6 times at 2-second intervals (12 seconds total) after calling `install_service()`. However, `install_service()` calls `valor-service.sh install`, which does a `launchctl unload` + `launchctl load` cycle. With `ThrottleInterval: 10`, launchd may not spawn the new process for up to 10 seconds after the load — and the bridge then needs a few more seconds to initialize.

**Current behavior:**
The update script reports a warning that makes it appear the restart failed, even though the bridge is running fine moments later.

**Desired outcome:**
The health check waits long enough for launchd's throttle interval plus bridge initialization, and reports accurately.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

The fix is isolated to `scripts/update/run.py` — increase the polling window in the existing retry loop.

## Solution

### Key Elements

- **Extended polling window**: Increase the retry loop in `run.py` from 6x2s (12s) to cover the launchd throttle interval plus startup time
- **Smarter polling**: Use more iterations with shorter intervals for responsiveness, total window ~20s

### Flow

**Current flow (broken):**
`install_service()` → poll 6x at 2s intervals (12s) → bridge not yet running → false warning

**Fixed flow:**
`install_service()` → poll 10x at 2s intervals (20s) → bridge detected running → accurate report

### Technical Approach

In `scripts/update/run.py`, the health check loop at lines 248-255:

```python
for _ in range(6):
    time.sleep(2)
    result.service_status = service.get_service_status(project_dir)
    if result.service_status.running:
        break
```

Change to:

```python
for _ in range(10):
    time.sleep(2)
    result.service_status = service.get_service_status(project_dir)
    if result.service_status.running:
        break
```

This extends the window from 12s to 20s, which covers:
- launchd ThrottleInterval (10s)
- Bridge initialization (Telegram connection, ~3-5s)
- Safety margin (~5s)

The 2-second poll interval keeps reporting responsive — we detect the bridge within 2s of it starting.

## Rabbit Holes

- Don't add a sophisticated health check protocol (HTTP endpoint, socket check, etc.) — `pgrep` is sufficient for "is the process running"
- Don't add log file parsing to detect "Connected to Telegram" — process existence is the right level of abstraction for the update script
- Don't change `ThrottleInterval` — 10s is a reasonable anti-thrash value

## Risks

### Risk 1: Still too short on slow machines
**Impact:** On a very slow machine, 20s might still not be enough
**Mitigation:** 20s is generous for the observed behavior (bridge connects in ~5s after launch). If this proves insufficient on specific hardware, the window can be increased further.

## No-Gos (Out of Scope)

- Adding HTTP health check endpoints to the bridge
- Changing `ThrottleInterval` in the plist
- Adding log-based health detection
- Refactoring `install_service()` to return timing information

## Update System

This change is self-contained within the update system itself. After git pull, the new `run.py` is used automatically on the next update. No additional migration or propagation steps needed.

## Documentation

### Inline Documentation
- [ ] Add a comment in `run.py` explaining the polling window calculation (ThrottleInterval + startup + margin)

### Feature Documentation
No new feature documentation needed — this is a bug fix to existing update infrastructure.

## Success Criteria

- [ ] Health check polling window covers at least 20 seconds total
- [ ] `scripts/update/run.py --full` reports bridge as running after a successful restart
- [ ] No false "Bridge not running" warnings when bridge starts normally
- [ ] Comment documents the timing rationale
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (health-check)**
  - Name: health-check-builder
  - Role: Extend the polling loop in run.py
  - Agent Type: builder
  - Resume: true

- **Validator (health-check)**
  - Name: health-check-validator
  - Role: Verify the polling window and behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend health check polling window
- **Task ID**: build-health-check
- **Depends On**: none
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/update/run.py`, change `range(6)` to `range(10)` in the bridge status polling loop (around line 251)
- Add a comment above the loop explaining the timing: ThrottleInterval (10s) + bridge startup (~5s) + margin (~5s) = 20s window
- Keep the `time.sleep(2)` interval unchanged

### 2. Validate health check fix
- **Task ID**: validate-health-check
- **Depends On**: build-health-check
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the polling loop uses `range(10)` with `sleep(2)` (20s total window)
- Verify the comment explains the timing rationale
- Run validation commands

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-health-check
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -A5 'Wait for bridge' scripts/update/run.py` - verify polling loop parameters
- `python -c "import ast; t=ast.parse(open('scripts/update/run.py').read()); print('OK')"` - verify syntax
- `grep 'range(10)' scripts/update/run.py` - verify extended range
- `grep 'ThrottleInterval\|throttle\|startup' scripts/update/run.py` - verify timing comment exists
