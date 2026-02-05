---
status: In Progress
type: bug
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/54
---

# Bridge KeepAlive Fix (Auto-Restart After SIGTERM)

## Problem

When the bridge self-restarts (via the restart flag mechanism after `remote-update.sh` detects new commits), it sends itself SIGTERM, which triggers a graceful shutdown with exit code 0. The launchd plist uses `KeepAlive > SuccessfulExit: false`, which only restarts the process on non-zero exit codes. Launchd treats the clean exit as intentional and does not restart the bridge.

**Current behavior:**
The bridge goes down silently after a self-triggered restart and stays down until manually restarted. This causes missed messages in Telegram groups. On 2026-02-05, a ~20 minute outage occurred between the self-SIGTERM at 10:36:18 and manual restart at 10:56:14.

**Desired outcome:**
The bridge always restarts after any exit, whether from SIGTERM, crash, or intentional restart. The `ThrottleInterval: 10` already prevents rapid restart loops.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

This is a one-line fix in the plist template plus propagation to the installed plist.

## Solution

### Key Elements

- **Plist template fix**: Change the `KeepAlive` value from conditional (`SuccessfulExit: false`) to unconditional (`true`) in `scripts/valor-service.sh`
- **Service reinstall**: The fix takes effect when `valor-service.sh install` regenerates and reloads the plist

### Flow

**Fix flow:**
Developer changes plist template → `valor-service.sh install` writes new plist → `launchctl` reloads → bridge now restarts on any exit code

**Runtime flow after fix:**
Bridge self-SIGTERMs → exits with code 0 → launchd restarts (KeepAlive: true) → bridge reconnects to Telegram

### Technical Approach

- Change lines 244-248 of `scripts/valor-service.sh` from:
  ```xml
  <key>KeepAlive</key>
  <dict>
      <key>SuccessfulExit</key>
      <false/>
  </dict>
  ```
  to:
  ```xml
  <key>KeepAlive</key>
  <true/>
  ```
- Run `./scripts/valor-service.sh install` to apply the change to the live plist
- The `ThrottleInterval: 10` remains unchanged — it prevents rapid restart loops

## Rabbit Holes

- Don't change the SIGTERM/exit code behavior in `_trigger_restart()` or `_graceful_shutdown()` — the current graceful shutdown is correct, the bug is in the plist
- Don't add custom restart logic in Python — launchd's KeepAlive is the right mechanism
- Don't use `os.execv()` to re-exec in-place — launchd should manage process lifecycle

## Risks

### Risk 1: Unintentional restart loops
**Impact:** If the bridge crashes immediately on startup, KeepAlive: true would restart it repeatedly
**Mitigation:** `ThrottleInterval: 10` limits restarts to once per 10 seconds. If the bridge can't start, it will retry every 10s until the issue is fixed — this is preferable to staying down silently.

## No-Gos (Out of Scope)

- Changing the exit code in `_trigger_restart()` (Option B from the issue)
- Replacing SIGTERM with `os.execv()` (Option C from the issue)
- Adding health-check-based restart logic
- Modifying the bridge shutdown sequence

## Update System

The update system generates the plist via `valor-service.sh install`. After this fix, any machine running `/update` will get the new plist template via git pull, and the next `valor-service.sh install` call will apply it. No additional migration steps needed — the fix propagates naturally through the existing update flow.

## Agent Integration

No agent integration required — this is a launchd plist change to bridge process management. The agent's tools and MCP servers are unaffected.

## Documentation

### Inline Documentation
- [ ] Add a comment in `valor-service.sh` above the KeepAlive line explaining why unconditional `true` is used

### Feature Documentation
No new feature documentation needed — this is a bug fix to existing infrastructure.

## Success Criteria

- [ ] `scripts/valor-service.sh` plist template uses `<key>KeepAlive</key><true/>` (unconditional)
- [ ] After `./scripts/valor-service.sh install`, the installed plist at `~/Library/LaunchAgents/com.valor.bridge.plist` shows `<key>KeepAlive</key><true/>`
- [ ] Bridge restarts automatically after self-triggered SIGTERM (exit code 0)
- [ ] Bridge restarts automatically after a crash (non-zero exit code)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (plist-fix)**
  - Name: plist-builder
  - Role: Change the KeepAlive value in valor-service.sh
  - Agent Type: builder
  - Resume: true

- **Validator (plist-fix)**
  - Name: plist-validator
  - Role: Verify the plist change and restart behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix KeepAlive in plist template
- **Task ID**: build-plist
- **Depends On**: none
- **Assigned To**: plist-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `scripts/valor-service.sh` lines 244-248: replace the `KeepAlive` dict block with `<key>KeepAlive</key><true/>`
- Add a brief inline comment explaining the choice of unconditional KeepAlive
- Run `./scripts/valor-service.sh install` to apply

### 2. Validate plist fix
- **Task ID**: validate-plist
- **Depends On**: build-plist
- **Assigned To**: plist-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `scripts/valor-service.sh` contains `<key>KeepAlive</key>` followed by `<true/>`
- Verify installed plist at `~/Library/LaunchAgents/com.valor.bridge.plist` matches
- Verify bridge is running after install
- Run all validation commands

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-plist
- **Assigned To**: plist-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -A1 'KeepAlive' scripts/valor-service.sh | grep -q '<true/>'` - verify template uses unconditional KeepAlive
- `grep -A1 'KeepAlive' ~/Library/LaunchAgents/com.valor.bridge.plist | grep -q '<true/>'` - verify installed plist
- `./scripts/valor-service.sh status` - verify bridge is running
- `grep 'ThrottleInterval' scripts/valor-service.sh` - verify throttle still present
