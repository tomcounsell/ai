---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-05-22
tracking: https://github.com/tomcounsell/ai/issues/1407
last_comment_id:
---

# Worker Watchdog launchd Bootstrap Fix

## Problem

The standalone worker (`python -m worker`) is supposed to be auto-restarted by macOS launchd (`KeepAlive=true`) and, as a backstop, actively revived by the external `monitoring/worker_watchdog.py` agent within two ticks (4 minutes). On Macs where `valor-service.sh stop_worker` + `start_worker` has cycled the service, neither path works: launchd's `KeepAlive` no longer fires on exit, and the watchdog's `launchctl kickstart` returns `rc=113, Could not find service "com.valor.worker" in domain for user gui: 501`. The worker stays down until a human manually runs `worker-start`.

**Current behavior:**
- `scripts/valor-service.sh:710` calls `launchctl load "$WORKER_PLIST_PATH"` (legacy macOS <12 path).
- `scripts/valor-service.sh:743` (stop_worker) calls `launchctl bootout gui/<uid>/com.valor.worker` (modern macOS 12+ path).
- After a stop/start cycle the service is registered via the legacy `load` path and is invisible to `gui/<uid>` queries.
- When the worker exits: `KeepAlive` does not fire, watchdog `kickstart` returns rc=113, `enable + kickstart` also returns rc=113, watchdog escalates to L4 CRITICAL and gives up.
- Observed 2026-05-22 08:51–08:56 UTC: worker down ~6 minutes until manual intervention.

**Desired outcome:**
- After a `worker-stop && worker-start` cycle, `launchctl print gui/<uid>/com.valor.worker` lists the service in the gui domain.
- If the worker is killed (even with SIGKILL), launchd restarts it within `ThrottleInterval` (10s) via `KeepAlive`, with no watchdog involvement.
- If `KeepAlive` somehow fails to restart the worker, the watchdog detects rc=113 from `kickstart` and self-heals by running `launchctl bootstrap gui/<uid> <plist>`, then retrying `kickstart` — without operator intervention.
- `logs/worker_watchdog.log` never shows "Could not find service" CRITICAL during a normal stop/start cycle.

## Freshness Check

**Baseline commit:** 6974997 (worktree branch session/sdlc-1407 off main)
**Issue filed at:** 2026-05-22T09:08:54Z (~4 hours before plan time)
**Disposition:** Minor drift

**File:line references re-verified:**
- `scripts/valor-service.sh:710` — issue claimed `launchctl load "$WORKER_PLIST_PATH"` — still holds (verified line 710 reads `launchctl load "$WORKER_PLIST_PATH"`).
- `scripts/valor-service.sh:732` — issue claimed `stop_worker()` calls `launchctl bootout` here — minor drift: function starts at 732, but the `bootout` call is at line 743. Same function, same claim.
- `monitoring/worker_watchdog.py::_handle_missing_worker()` — claim "no bootstrap recovery path" — still holds (function ends at line 399, only kickstart and enable are attempted).

**Cited sibling issues/PRs re-checked:**
- #1311 — CLOSED 2026-05-07 (passive watchdog defeats KeepAlive). Still relevant as background.
- #1315 — MERGED 2026-05-07 (active recovery via kickstart). The PR this issue is a follow-up to. The current bug is a regression caused by `start_worker()` not being updated to use the modern `bootstrap` API alongside the `bootout` modernization that already happened.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --oneline --since="2026-05-22T09:08:54Z" -- scripts/valor-service.sh monitoring/worker_watchdog.py` returned zero rows.

**Active plans in `docs/plans/` overlapping this area:** none.
Adjacent plans (`worker-kickstart-race.md`, `worker-lifecycle-cleanup.md`, etc.) cover different worker concerns; none touch the load↔bootstrap API choice.

**Notes:** Issue line number 732 should be read as "the function starting at 732" — the actual `bootout` call is at 743. Plan uses 743 from here on. Also surfaces a sibling pattern: `scripts/valor-service.sh:214` (bridge start) and `:663` (uninstall path uses `launchctl unload`) have the same load↔bootstrap mismatch. Bridge has its own watchdog (`monitoring/bridge_watchdog.py`) — out of scope for this plan; tagged in No-Gos.

## Prior Art

- **#1311 (closed 2026-05-07)**: "worker-watchdog observes but never recovers" — identified passive observation as insufficient, proposed active recovery. Outcome: superseded by #1315.
- **PR #1315 (merged 2026-05-07)**: "Worker watchdog: active recovery via launchctl kickstart (#1311)" — added the L1→L4 escalation chain in `monitoring/worker_watchdog.py`. This plan is a follow-up: the kickstart chain works correctly when the service IS registered in the gui domain, but `start_worker()` uses the legacy `load` path, leaving the service unregistered there. The fix is two-fold: (a) modernize `start_worker()`, (b) make the watchdog self-healing if anyone else registers the service via `load` in the future.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1315 | Added L2 (kickstart) + L3 (enable+kickstart) + L4 (CRITICAL) recovery escalation in the watchdog. | Implicitly assumed the worker service would always be registered in `gui/<uid>/` so `kickstart` could find it. Did not audit how `start_worker()` registers the service — `launchctl load` writes to a legacy path that `kickstart gui/<uid>/...` cannot address. Coverage gap: no test for "service unregistered in gui domain" returned rc=113. |

**Root cause pattern:** The watchdog's escalation chain only operates inside the modern launchd domain (`gui/<uid>/`). Any code path that registers the service outside that domain (legacy `launchctl load`, or absence of registration entirely) silently invalidates the entire chain. The fix must ensure (a) the registration path always lands in the modern domain, and (b) the watchdog can detect and recover from "not in domain" by registering itself.

## Research

No external research needed — this is a macOS launchctl API choice (`load` vs `bootstrap`) with stable, well-documented semantics. The relevant context is already in the issue body and the `launchctl(1)` man page (`man launchctl` on macOS confirms `bootstrap`/`bootout` superseded `load`/`unload` in macOS 10.10 and `load`/`unload` are deprecated since macOS 12).

Codebase already uses `launchctl bootstrap` in `scripts/install_worker.sh:141`, `scripts/install_email_bridge.sh:230`, `scripts/install_nightly_tests.sh:47`, `scripts/install_sdlc_reflection.sh:41`, and `scripts/remote-update.sh:217,226`. The `valor-service.sh` runtime helpers are the only outliers.

## Data Flow

This is a control-plane fix, not a data-flow change. The "data" is the launchd service registration record. Tracing the registration lifecycle clarifies what breaks.

1. **Worker installed (first time)**: `scripts/install_worker.sh:141` runs `launchctl bootstrap gui/<uid> <plist>` → service registered in `gui/<uid>/com.valor.worker`. Correct.
2. **Worker stopped** (`valor-service.sh stop_worker`): `launchctl bootout gui/<uid>/com.valor.worker` → registration removed from gui domain. Correct.
3. **Worker started** (`valor-service.sh start_worker`): `launchctl load "$WORKER_PLIST_PATH"` → registration goes to **legacy** path, NOT `gui/<uid>/`. **BUG**.
4. **Worker exits unexpectedly**: launchd does not honor `KeepAlive` because the registration is in the wrong domain. Worker stays dead.
5. **Watchdog runs** (every 120s): calls `_get_worker_pid()` → returns None → `_handle_missing_worker()` → L2 `kickstart gui/<uid>/com.valor.worker` → rc=113 "not in domain". L3 `enable + kickstart` → still rc=113. L4 CRITICAL.

After the fix:
3'. **Worker started**: `launchctl bootstrap gui/<uid> "$WORKER_PLIST_PATH"` → registration lands in `gui/<uid>/`. Correct.
4'. Worker exit → launchd honors `KeepAlive` → restart in ~10s.
5'. Watchdog never needs to fire. If it does fire (e.g., launchd itself glitches), `kickstart` succeeds.

If the watchdog ever encounters rc=113 from `kickstart` again (e.g., a future regression), the new L2.5 step calls `launchctl bootstrap gui/<uid> <plist>` and retries `kickstart`, healing the registration mismatch automatically.

## Architectural Impact

- **New dependencies**: None — uses launchctl, already present on every Mac.
- **Interface changes**: None — `start_worker()` and `_handle_missing_worker()` keep their signatures.
- **Coupling**: Very slight increase — `monitoring/worker_watchdog.py` now needs to know the plist path (`~/Library/LaunchAgents/com.valor.worker.plist`) to call `bootstrap`. This is the same path `install_worker.sh` writes, and the same constant `valor-service.sh` already uses. Added as a module-level constant alongside `WORKER_LAUNCHD_LABEL`.
- **Data ownership**: Unchanged — launchd remains the owner of service registration.
- **Reversibility**: Trivial — revert the two file edits.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is concrete, defined by the issue's Acceptance Criteria)
- Review rounds: 1 (single PR review)

The change is two file edits (~30 lines total) plus one new test. The risk surface is small and the rollback is one revert away.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| macOS host (development & test) | `uname -s \| grep -q Darwin` | `launchctl` only exists on macOS; tests mock subprocess but the integration validation is macOS-only. |
| Worker plist installed at expected path | `test -f "$HOME/Library/LaunchAgents/com.valor.worker.plist"` | Integration validation needs an installed plist; CI tests run with mocks and don't require this. |

## Solution

### Key Elements

- **`scripts/valor-service.sh::start_worker()` modernization**: Replace `launchctl load` with `launchctl bootstrap gui/<uid> <plist>`, mirroring `stop_worker()`'s use of `bootout` and matching the pattern already used by every other install script in this repo. The existing `is_worker_launchd_loaded` check (which uses `launchctl list`, a domain-agnostic query) stays; when true, `kickstart` is still the right call.
- **`monitoring/worker_watchdog.py` self-healing**: Add an L2.5 bootstrap-recovery step. When `kickstart` returns non-zero with stderr containing "Could not find service", attempt `launchctl bootstrap gui/<uid> <plist>` (reading the plist path from a new module constant), then retry `kickstart`. This makes the watchdog resilient to any future regression of the same class.
- **Test coverage**: One new unit test in `tests/unit/test_worker_watchdog.py` covering the rc=113 → bootstrap → retry path. Existing L1/L2/L3/L4 tests stay green.

### Flow

**Worker dies** → launchd `KeepAlive` restarts it (10s) → done.

**Fallback path** (KeepAlive somehow fails):
Watchdog tick → worker missing → L1 (one tick grace) → L2 kickstart → success? → done.
L2 fails with rc=113 → **L2.5 bootstrap + retry kickstart** → success? → done.
L2.5 fails → L3 enable+kickstart → L4 CRITICAL (unchanged terminal state).

### Technical Approach

- In `scripts/valor-service.sh::start_worker()` (line 708–713): replace the `launchctl load "$WORKER_PLIST_PATH"` branch with `launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME" 2>/dev/null; launchctl bootstrap "gui/$(id -u)" "$WORKER_PLIST_PATH"`. The defensive `bootout` ensures the bootstrap doesn't fail with "service already bootstrapped" if a partial registration exists. Match the pattern at `valor-service.sh:389-390` (bridge restart helper) and `install_worker.sh:78-79,141`.
- In `monitoring/worker_watchdog.py`:
  - Add module constant: `WORKER_PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{WORKER_LAUNCHD_LABEL}.plist"`.
  - Add function `_bootstrap_worker() -> bool` that runs `launchctl bootstrap gui/<uid> <plist>` and returns `returncode == 0`. Mirror the structure of `_kickstart_worker()` and `_enable_worker()`. Log success/failure at INFO/ERROR.
  - In `_kickstart_worker()`, distinguish rc=113 / "Could not find service" stderr from other failures. Return a richer result (either a tri-state or expose the stderr) so `_handle_missing_worker` can detect the unregistered-service condition. Implementation choice: return `subprocess.CompletedProcess`-like data from a private helper and have a thin `_kickstart_worker() -> bool` wrapper for the existing call sites.
  - In `_handle_missing_worker()`, between current L2 and L3: if L2's kickstart failed AND the failure stderr indicates "Could not find service" AND the plist file exists, call `_bootstrap_worker()`. If it succeeds, retry `_kickstart_worker()` and verify. On success: log "Worker revived via bootstrap+kickstart (PID=...)" and clear the down-tick counter. On failure: fall through to L3 as today.
- The bootstrap-recovery branch is gated on plist file existence so the watchdog never spuriously bootstraps a nonexistent service (e.g., on a machine where the worker was uninstalled).
- Behavior under `_is_operator_disabled() == True` is unchanged — the early return at line 411 still short-circuits before recovery.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `monitoring/worker_watchdog.py` for `except Exception:` blocks added by the new `_bootstrap_worker()` function. Mirror `_kickstart_worker()`'s logging style: every failure logs `logger.error` with rc and stderr; timeouts log a distinct message. Tests assert log content.
- [ ] No silent `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] If `WORKER_PLIST_PATH` does not exist on disk, `_handle_missing_worker()` MUST NOT call `_bootstrap_worker()` — fall through to L3 unchanged. Test asserts this branch.
- [ ] If `launchctl bootstrap` itself times out, the watchdog must not hang the tick — `subprocess.run(..., timeout=10)` matches the existing pattern.

### Error State Rendering
- [ ] When all recovery levels fail, the CRITICAL log line must mention "bootstrap" so operators see which step was attempted (today it only mentions kickstart+enable). Add to the L4 reason string when bootstrap was attempted.

## Test Impact

- [ ] `tests/unit/test_worker_watchdog.py::TestKickstartHelper::test_kickstart_failure_returncode` — UPDATE: now asserts the helper exposes stderr (or returns a richer object) so callers can distinguish rc=113 from other failures. Existing return-True/False contract preserved via the thin wrapper.
- [ ] `tests/unit/test_worker_watchdog.py::TestHandleMissingWorker::test_l3_runs_enable_then_kickstart_when_l2_fails` — UPDATE: ensure mocked `_kickstart_worker` returns a non-113 failure (or that the rc=113 branch is bypassed by missing plist) so the test still exercises L3 specifically. Add explicit `os.path.exists` patch.
- [ ] `tests/unit/test_worker_watchdog.py::TestHandleMissingWorker::test_l4_writes_critical_redis_key` — UPDATE: extend the reason-string assertion to accept the new "bootstrap+kickstart+enable" wording when applicable.
- [ ] `tests/unit/test_worker_watchdog.py` — ADD a new test class `TestBootstrapRecovery` with cases: (a) rc=113 + plist exists → bootstrap called → kickstart retried → worker revived → counter cleared; (b) rc=113 + plist missing → no bootstrap, fall through to L3; (c) rc=113 + bootstrap fails → fall through to L3; (d) non-113 kickstart failure → bootstrap NOT called → fall through to L3.
- [ ] No existing tests for `scripts/valor-service.sh::start_worker()` exist (shell script, no test harness). The Verification table validates manually via `launchctl print`.

## Rabbit Holes

- **Rewriting valor-service.sh to use modern API everywhere**: line 214 (`bridge` start) and line 663 (`unload` in uninstall) have the same legacy↔modern mismatch, but bridge has its own watchdog with separate semantics. Fixing them is a separate, parallel concern — filing as `[SEPARATE-SLUG]` candidate, not in this plan.
- **Auditing every launchd-managed service in the repo for consistency**: tempting but unbounded. The worker is what's known-broken; ship the targeted fix.
- **Adding integration tests that actually call launchctl**: macOS-only, sandbox-hostile, would require a real plist install. Mocked unit tests + manual Verification checklist cover the behavior; an integration test would be high-friction with low marginal value.
- **Reworking the L1–L4 abstraction**: the escalation chain is fine; the bug is a missing branch. Don't refactor what's working.

## Risks

### Risk 1: `launchctl bootstrap` fails when service is already bootstrapped
**Impact:** `start_worker()` would error out on the first run after a successful stop/start cycle, breaking the existing happy path.
**Mitigation:** Run `launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME" 2>/dev/null || true` immediately before `bootstrap` in `start_worker()`. `bootout` is idempotent (returns success even if nothing was bootstrapped) thanks to `2>/dev/null || true`. This mirrors the install_worker.sh:78-79 and remote-update.sh:215-217 patterns.

### Risk 2: Plist path drift
**Impact:** If `~/Library/LaunchAgents/com.valor.worker.plist` is ever moved (e.g., to a system-wide LaunchDaemon location), the new constant in `worker_watchdog.py` would be wrong and bootstrap-recovery would silently fail.
**Mitigation:** The plist path is already a convention used in `valor-service.sh:55-56`. Document the assumption in a code comment alongside the new constant. The plist-existence gate ensures a missing plist falls through to L3 cleanly rather than crashing.

### Risk 3: Watchdog regression — bootstrap loop
**Impact:** If `bootstrap` succeeds but `kickstart` still fails (some unknown launchd state), the watchdog could retry bootstrap every tick.
**Mitigation:** The bootstrap step lives between L2 and L3. If kickstart-after-bootstrap fails, the code falls through to L3 (enable+kickstart) and then L4 — the same terminal escalation as today. The down-tick counter still drives L4 CRITICAL after 3 ticks. No new loop.

### Risk 4: Operator-disable race
**Impact:** If an operator runs `worker-disable` (which calls `launchctl disable`), the watchdog's L2.5 bootstrap could re-enable the service.
**Mitigation:** `_is_operator_disabled()` is checked at the top of the watchdog loop (line 411) and short-circuits the entire recovery chain. `_handle_missing_worker()` is only called when the operator has NOT disabled the worker. Bootstrap inside `_handle_missing_worker()` cannot fire when disabled. Add an explicit test for this.

## Race Conditions

No race conditions identified. The fix is purely synchronous: shell commands in `start_worker()` run sequentially; the watchdog's recovery chain in `_handle_missing_worker()` runs sequentially within a single tick. The down-tick counter is the only shared state and its read-modify-write is already wrapped in best-effort try/except — the existing behavior is preserved.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1407] Bridge start path (`scripts/valor-service.sh:214`) also uses `launchctl load` — same class of bug, different surface. Will be addressed in a follow-up issue (to be filed) covering bridge + uninstall paths. This plan deliberately keeps scope to the worker so the fix can land fast and be verified in isolation. Note: tag uses #1407 self-reference because the follow-up issue does not yet exist; if reviewers prefer, will file the follow-up issue first and update this tag.
- [EXTERNAL] Manual verification on each Mac post-deploy: `launchctl print gui/<uid>/com.valor.worker` shows the service registered. The agent cannot run launchctl on remote machines; operators verify after `/update` runs.
- [SEPARATE-SLUG #1407] Adding a structural lint to ban `launchctl load`/`unload` in shell scripts. Tempting but separate hygiene work; this plan ships the bug fix only.

## Update System

This change ships via the standard `/update` flow (no special migration). After the PR merges:
- Each machine runs `git pull` and `./scripts/valor-service.sh worker-restart`.
- The first `worker-restart` post-update will (a) bootout the legacy-registered service, (b) bootstrap into the gui domain.
- `scripts/remote-update.sh:213-226` already runs `launchctl kickstart` on the worker plist and falls back to `bootstrap` — no changes needed to the update script.
- Watchdog launchd agent (`com.valor.worker.watchdog`) is restarted as part of the worker-restart sequence, picking up the new `worker_watchdog.py` code.

No new dependencies or config files. No data migration.

## Agent Integration

No agent integration required — this is an infrastructure fix to the worker launchd lifecycle. The agent neither calls nor observes `launchctl` directly. No MCP server changes, no CLI entry points, no bridge changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` if it documents the launchd registration — add a sentence noting the bootstrap-based registration model.
- [ ] Update `docs/features/bridge-self-healing.md` (or its worker counterpart, if present) to mention the watchdog's new L2.5 bootstrap-recovery step.

### External Documentation Site
- No external docs site for this repo.

### Inline Documentation
- [ ] Code comment in `start_worker()` explaining why `bootout + bootstrap` is preferred over `load` (cite macOS 12+ deprecation).
- [ ] Code comment in `_handle_missing_worker()` for the L2.5 branch explaining the rc=113 detection and self-healing intent.
- [ ] Module-level docstring update in `monitoring/worker_watchdog.py` to extend the L1→L4 description with the new L2.5 step.

## Success Criteria

- [ ] `scripts/valor-service.sh::start_worker()` calls `launchctl bootstrap gui/<uid> <plist>` (preceded by defensive `bootout`), not `launchctl load`.
- [ ] After `./scripts/valor-service.sh worker-stop && ./scripts/valor-service.sh worker-start`, `launchctl print gui/<uid>/com.valor.worker` shows the service as registered (manual verification on at least one Mac).
- [ ] Killing the worker with `kill -9 $(pgrep -f "python -m worker")` results in launchd restarting it within 15 seconds (manual verification on at least one Mac).
- [ ] `monitoring/worker_watchdog.py::_handle_missing_worker()` detects rc=113 from `kickstart` and runs `launchctl bootstrap gui/<uid> <plist>` before escalating to L3.
- [ ] New unit test class `TestBootstrapRecovery` in `tests/unit/test_worker_watchdog.py` covers the four bootstrap-recovery branches (revive, no-plist fallthrough, bootstrap-fails fallthrough, non-113 fallthrough).
- [ ] All existing tests in `tests/unit/test_worker_watchdog.py` pass unchanged or with the minor UPDATE noted in Test Impact.
- [ ] `logs/worker_watchdog.log` shows no "Could not find service" CRITICAL during a normal stop/start cycle (manual verification post-deploy).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (worker-watchdog-fix)**
  - Name: `watchdog-builder`
  - Role: Modernize `start_worker()` and add L2.5 bootstrap-recovery to the watchdog, plus the new unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator (worker-watchdog-fix)**
  - Name: `watchdog-validator`
  - Role: Verify all unit tests pass, lint is clean, no behavioral regressions in existing L1–L4 tests.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `watchdog-docs`
  - Role: Update bridge-worker-architecture / bridge-self-healing docs and inline comments.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Modernize `start_worker()` in valor-service.sh
- **Task ID**: build-start-worker
- **Depends On**: none
- **Validates**: manual `launchctl print gui/<uid>/com.valor.worker` after stop/start
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace lines 708-713 to run `launchctl bootout "gui/$(id -u)/$WORKER_PLIST_NAME" 2>/dev/null || true` then `launchctl bootstrap "gui/$(id -u)" "$WORKER_PLIST_PATH"` when the plist exists; keep the `kickstart` fallback for the already-bootstrapped case (use `is_worker_launchd_loaded` to choose).
- Add an inline comment citing macOS 12+ deprecation of `launchctl load`.
- Verify the change shellchecks (`shellcheck scripts/valor-service.sh`) if shellcheck is available; otherwise visual review.

### 2. Add L2.5 bootstrap-recovery to worker_watchdog.py
- **Task ID**: build-watchdog-bootstrap
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_watchdog.py`
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module constant `WORKER_PLIST_PATH` (Path-based, sibling to `WORKER_LAUNCHD_LABEL`).
- Add `_bootstrap_worker() -> bool` helper modeled on `_kickstart_worker()`.
- Refactor `_kickstart_worker()` to expose stderr/rc to its caller (either a tuple return or a sibling helper `_kickstart_worker_detailed()`); keep the existing `_kickstart_worker() -> bool` wrapper for the L1/L3/L4 call sites that don't care.
- Insert L2.5 logic in `_handle_missing_worker()` between current L2 and L3: detect rc=113 + plist exists → bootstrap → retry kickstart → verify → clear counter on success, else fall through to L3.
- Extend module docstring (lines 14–23) to describe L2.5.

### 3. Update existing unit tests + add TestBootstrapRecovery
- **Task ID**: build-tests
- **Depends On**: build-watchdog-bootstrap
- **Validates**: `pytest tests/unit/test_worker_watchdog.py -v`
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `test_kickstart_failure_returncode` to match the new richer return contract.
- Update `test_l3_runs_enable_then_kickstart_when_l2_fails` to patch `os.path.exists`/`Path.exists` so the L2.5 branch is bypassed and L3 is still the test focus.
- Update `test_l4_writes_critical_redis_key` reason-string assertion if the L4 log line wording changes.
- Add `TestBootstrapRecovery` class with four cases (revive / no-plist / bootstrap-fails / non-113).
- Add `test_operator_disable_blocks_bootstrap_recovery` to confirm the disabled short-circuit still wins.

### 4. Validate
- **Task ID**: validate-watchdog
- **Depends On**: build-start-worker, build-watchdog-bootstrap, build-tests
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_watchdog.py -v` — must pass 100%.
- Run `python -m ruff check monitoring/worker_watchdog.py` and `python -m ruff format --check monitoring/worker_watchdog.py`.
- Visually inspect `scripts/valor-service.sh` diff for shell hygiene (quoting, error handling).
- Report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-watchdog
- **Assigned To**: watchdog-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` (or worker-specific doc if present) to mention bootstrap-based registration.
- Update bridge-self-healing equivalent for the worker, or create a stub if none exists.
- Verify inline comments from tasks 1 & 2 are present and accurate.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit suite touching worker/watchdog: `pytest tests/unit/test_worker_watchdog.py tests/unit/test_background_task_watchdog.py -v`.
- Confirm Success Criteria checkboxes that can be verified pre-merge.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Watchdog tests pass | `pytest tests/unit/test_worker_watchdog.py -x -q` | exit code 0 |
| Lint clean (watchdog) | `python -m ruff check monitoring/worker_watchdog.py` | exit code 0 |
| Format clean (watchdog) | `python -m ruff format --check monitoring/worker_watchdog.py` | exit code 0 |
| start_worker uses bootstrap | `grep -n 'launchctl bootstrap' scripts/valor-service.sh` | output contains `start_worker` context (line 710 region) |
| start_worker no longer uses `load` | `awk '/^start_worker\(\)/,/^}$/' scripts/valor-service.sh \| grep -c 'launchctl load'` | output contains 0 |
| Bootstrap helper present | `grep -n '_bootstrap_worker' monitoring/worker_watchdog.py` | exit code 0 |
| TestBootstrapRecovery present | `grep -n 'class TestBootstrapRecovery' tests/unit/test_worker_watchdog.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Scope of sibling bug**: should `scripts/valor-service.sh:214` (bridge start, same `launchctl load` bug) be fixed in this PR or filed as a separate issue? Default: separate issue for review isolation. Confirm.
2. **macOS-only watchdog**: the existing `worker_watchdog.py` already implicitly assumes macOS. Should the new `_bootstrap_worker()` add a `platform.system() == "Darwin"` guard, or rely on the launchd daemon's existing platform context? Default: no guard (matches existing helpers).
3. **Should the L4 CRITICAL reason string include "bootstrap" in the failure path**, or keep it as "kickstart+enable" for log-grep stability with existing dashboards/alerts? Default: extend the wording — current alerts already match on "WORKER WATCHDOG CRITICAL" prefix.
