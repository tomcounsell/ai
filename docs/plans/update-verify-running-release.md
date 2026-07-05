---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1898
last_comment_id:
---

# Update verifies the running-process release matches pulled HEAD

## Problem

On 2026-07-04 the Captain machine ran `/update`, which reported **✅ update OK @ 6b5b998a**. But the live bridge process kept reporting release `659756a4` — 52 commits behind, predating all 11 merges from the 2026-07-03/04 bug-slate. Sentry confirmed it: a production event at 2026-07-04T14:26:49Z with `server_name=Valor-the-Captain.local`, `release=659756a4463b`, `sys.argv=[.../bridge/telegram_bridge.py]`. The reason-aware interrupt copy (#1877), priming-liveness (#1878), wedge-nudge rung (#1879), and granite handshake fix (#1881) were all on disk but not in the running process, so Cuttlefish sessions kept wedging and kept sending the pre-#1877 hardcoded interrupt copy.

**Current behavior:**
Cron `/update` (the Telegram-triggered path and the 30-min polling cron) pulls code, runs migrations, sets a deferred restart flag, and reports success — all decoupled from whether the bridge or worker ever cycled onto the new code. The bridge has no cron-mode restart path at all; the worker's deferred restart can be starved by a wedged session and then silently expires after 1h. The update's "OK" makes no claim about the release the live processes are actually running.

**Desired outcome:**
`/update` verifies, after its restart step, that both the bridge and the worker are running code at the pulled HEAD, and it exits non-zero (loud, actionable) when a process still runs stale code. An update that fails to cycle the fleet onto the new code must not report OK.

## Freshness Check

**Baseline commit:** `63e43118`
**Issue filed at:** 2026-07-04T15:45:52Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/run.py:88-100` — `UpdateConfig.cron()` sets `do_service_restart=False` — still holds.
- `scripts/update/run.py:1555-1558` — cron mode sets restart flag instead of restarting — still holds.
- `scripts/update/run.py:1339` — bridge success gate checks only `service_status.running` — still holds.
- `scripts/update/service.py:71-105` — `get_service_status()` returns `running`/`pid`/`uptime`, no release — still holds.
- `agent/agent_session_queue.py:1209-1258` — `_check_restart_flag` / `_trigger_restart` (1h TTL, no-running-sessions gate) — still holds.
- `agent/agent_session_queue.py:1780`, `:2203` — only the worker loop consumes the flag — still holds.
- `bridge/telegram_bridge.py:2985` — bridge only *clears* a stale flag on startup, never triggers restart — still holds.
- `monitoring/sentry_config.py:61` — `release=git rev-parse HEAD` captured at process init — still holds (explains the frozen Sentry release).

**Cited sibling issues/PRs re-checked:**
- #1877, #1878, #1879, #1881 — the bug-slate merges whose code was on disk but not running; all merged before the issue. Context only; not blockers.

**Commits on main since issue was filed (touching referenced files):**
- `313724f3` "Fix Tier 0: plan-migration invariant (#1900, PR 1/2)" — irrelevant to restart/release logic (plan-migration hook).

**Active plans in `docs/plans/` overlapping this area:** none. (`consolidate_delivery_paths.md` and `granite_lossless_checkpoint_resume.md` touch delivery/PTY, not the update/restart path.)

**Notes:** No drift. All line references current at `63e43118`.

## Prior Art

- **#1767** "Worker watchdog fails to recover a U-state hung worker" (closed 2026-06-25) — hardened worker recovery, but did not touch how `/update` verifies the running release. Relevant only as prior evidence that wedged/hung processes are a recurring failure mode that starves the deferred-restart path.
- **#1815 / #1817 / #1877** (closed) — resilience and lifecycle work on wedge survival and interrupt messaging. These are the *payload* that failed to reach the running bridge; none of them added release verification to the updater.
- **PR #1832** "worker fault containment" (merged 2026-06-30) — added worker fault handling but no updater-side release check.

No prior issue or PR added a post-restart release-verification gate to `/update`. This is greenfield for the updater. The "Why Previous Fixes Failed" section is omitted — no prior attempt targeted this specific gap.

## Research

No relevant external findings — this is purely internal (macOS launchd, git rev-parse, Redis/Popoto, the repo's own update system). Proceeding with codebase context.

## Data Flow

Trace of how a code change reaches (or fails to reach) the running processes under cron `/update`:

1. **Entry point**: Telegram `/update` (or the 30-min polling cron) → `scripts/remote-update.sh` → `python -m scripts.update.run --cron`.
2. **Git pull** (`run.py` Step 3): `origin/main` pulled to disk; HEAD becomes the new SHA (e.g. `6b5b998a`).
3. **Migrations** (Step 3.6): run against the pulled code.
4. **Service management** (Step 5): `do_service_restart` is **False** in cron mode → the `if config.do_service_restart:` block is skipped entirely. Bridge and worker are NOT restarted.
5. **Restart flag** (`run.py:1555-1558`): `git.set_restart_requested()` writes `data/restart-requested` with a timestamp + commit count.
6. **Flag consumption** (worker only): `agent/agent_session_queue.py:1780`/`:2203` call `_check_restart_flag()` between sessions. Returns True only if flag < 1h old AND no session has `status="running"`. On True → `_trigger_restart()` sends SIGTERM → launchd KeepAlive respawns the **worker** on new code.
   - **Failure branch A (bridge):** the bridge never checks the flag. It keeps running old code until a full `/update` (`install_service`), a crash+KeepAlive respawn, or a watchdog restart. → the observed `659756a4` bridge.
   - **Failure branch B (worker):** if a session is wedged in `status="running"` for >1h, `_check_restart_flag()` returns False every cycle and then deletes the flag as stale. The worker also stays on old code, and nothing reports it.
7. **Success summary** (`run.py:1867-1906`): status derived from `result.success` (git + migrations + best-effort service checks). Reports "update successful" / "updated to {sha}". **No comparison of running-process release vs. HEAD.**

The fix inserts a new terminal step between (6) and (7): read each process's **boot SHA** and compare to HEAD.

## Architectural Impact

- **New durable signal (boot-SHA beacon):** the bridge and worker each record, at startup, the git SHA they were launched at, to a known location the updater can read without touching the process. This is additive.
- **Interface changes:** `service.get_service_status()` / `get_worker_status()` (or a new helper) gains a `boot_sha` field; a new `verify_running_release()` function in `scripts/update/service.py`. `run.py` gains a verification step.
- **Coupling:** low. The beacon is a one-line write at startup and a file read at verify time. No new runtime dependency between bridge and worker.
- **Data ownership:** each process owns its own beacon file; the updater is a read-only consumer + the process that resets HEAD.
- **Reversibility:** high. The verification step is a bounded gate; disabling it is a one-line revert. The beacon writes are inert if unread.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan critique, code review

**Interactions:**
- PM check-ins: 1-2 (confirm the cron-mode escalation policy — hard-fail vs. forced restart)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It runs against the repo's own update system, git, and launchd (already present on every machine). Executable proof (acceptance criterion 3) requires running on a bridge machine, which is captured as an `[EXTERNAL]` No-Go step, not a build-time prerequisite.

## Solution

### Key Elements

- **Boot-SHA beacon**: at startup the bridge writes its launch SHA to `data/bridge_boot_sha`, and the worker writes its launch SHA to `data/worker_boot_sha` (via `git rev-parse HEAD` at process init, mirroring `monitoring/sentry_config.py:61`). Each write includes the SHA and a timestamp so a stale/orphaned beacon is detectable.
- **Release-verification step** in the updater: a new `verify_running_release()` reads both beacons and compares them to the pulled HEAD. It runs as the last step of a service-restart run.
- **Mode-aware verdict**:
  - **Full mode** (`do_service_restart=True`): the restart already happened synchronously in Step 5. Verify bridge AND worker boot SHA == HEAD. On mismatch → `result.success = False`, loud error, exit non-zero.
  - **Cron mode** (`do_service_restart=False`): the restart is deferred. A single stale reading immediately after flag-set is expected (the process drains its session first), so verification distinguishes "a fresh restart is pending" (warn) from "the restart has been starved" (escalate). Escalation is bounded: if a restart flag has been pending across a configurable staleness window (or the beacon still lags HEAD after the flag's own TTL), escalate — either force the restart or hard-fail the run so the operator is alerted rather than left with a silent stale fleet.
- **Bridge self-restart path**: give the bridge a cron-mode restart path so a code change delivered by the polling cron actually reaches it (today only a full `/update` restarts the bridge). Either the bridge consumes the restart flag between message batches (idle-gated, mirroring the worker), or the cron-mode verification force-restarts a bridge whose boot SHA lags HEAD. The plan prefers the flag-consumption path for symmetry with the worker; the force-restart is the fallback the verification step performs when the flag path is starved.

### Flow

Cron `/update` runs → git pull to new HEAD → migrations → set restart flag → (worker cycles when idle; bridge cycles when idle) → **verify step reads `data/bridge_boot_sha` + `data/worker_boot_sha`** → both == HEAD? → report OK. One lags AND the pending window is exceeded → escalate (force restart or exit non-zero with "bridge running {stale} but HEAD is {new}").

### Technical Approach

- **Beacon write**: add a small helper (e.g. `monitoring/boot_beacon.py` or a function in `agent/agent_session_queue.py` alongside the flag helpers) that writes `{sha}\n{iso-timestamp}` to `data/{bridge,worker}_boot_sha`. Call it once at bridge startup (near `bridge/telegram_bridge.py:2985`, where the stale flag is already cleared) and once at worker startup (`worker/__main__.py`). Use `git rev-parse HEAD` with the same subprocess pattern as `monitoring/sentry_config.py:61` and `monitoring/crash_tracker.py:59`. Writes are best-effort (swallow FS errors, never crash startup).
- **Verify helper**: `scripts/update/service.py::verify_running_release(project_dir, head_sha) -> ReleaseCheck` returning per-process `{running, boot_sha, matches, beacon_age}`. Reads the beacon files; treats a missing/older-than-process beacon conservatively (see Race Conditions). Reuse `git.get_short_sha()` for HEAD.
- **run.py wiring**:
  - Full mode: call `verify_running_release()` after the Step 5 restart+poll block; on any process mismatch append an error and set `result.success = False`.
  - Cron mode: call it after the restart-flag set; classify as `pending` (beacon predates flag, flag fresh) → warning, or `starved` (flag older than the pending window, or beacon still stale) → escalate per the chosen policy (Open Question 1).
- **Escalation implementation** (cron starved): the safest bounded action is to perform the same synchronous restart the full path uses (`service.install_service` for the bridge, `restart_worker` for the worker) and re-verify once; if it still mismatches, hard-fail. This converts a silent stale fleet into either a fixed fleet or a loud failure.
- **Summary surfacing**: extend the cron summary (`run.py:1867-1906`) so a release mismatch/pending appears in the Telegram status line, not only in the log file.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Beacon writes are wrapped in best-effort try/except (like `_green_heartbeat_write` at `worker/__main__.py:260`). Add a test asserting a write failure (unwritable `data/`) logs a warning and does NOT crash startup.
- [ ] `verify_running_release()` must not raise on a missing beacon file — test the missing-file path returns a well-formed "unknown/stale" result, not an exception.

### Empty/Invalid Input Handling
- [ ] Test `verify_running_release()` with: missing beacon, empty beacon, malformed beacon (no timestamp), beacon SHA == HEAD, beacon SHA != HEAD, beacon older than process start.
- [ ] Test the cron classifier with a fresh flag + stale beacon (→ pending/warn) and a stale flag + stale beacon (→ starved/escalate).

### Error State Rendering
- [ ] Full-mode mismatch surfaces a non-zero exit and a clear error string naming both SHAs. Test the exit code and message.
- [ ] Cron-mode escalation surfaces the mismatch in the Telegram summary line (not only the attached log). Test the summary builder includes the release warning.

## Test Impact

- [ ] `tests/unit/` (update-system tests, e.g. `test_update_service.py` / `test_update_run.py` if present) — UPDATE: add coverage for `verify_running_release()` and the mode-aware verdict; assert full-mode mismatch sets `result.success=False`.
- [ ] Worker/bridge startup tests that assert startup side effects — UPDATE: add assertion that the boot-SHA beacon is written at startup.
- [ ] `agent/agent_session_queue.py` restart-flag tests (if the bridge gains flag consumption) — UPDATE: assert the bridge idle path consumes the flag and triggers restart.

If no dedicated update-system unit test file exists, this is greenfield for that module — create `tests/unit/test_update_release_verify.py`. No existing test asserts release verification today, so nothing needs DELETE/REPLACE; changes are additive to startup and the updater's terminal step.

## Rabbit Holes

- **Do not rebuild the restart mechanism.** The launchd KeepAlive + SIGTERM graceful-restart model works; the gap is verification and the missing bridge path. Resist redesigning the whole restart lifecycle.
- **Do not try to read the running process's in-memory code SHA.** A file/Redis beacon written at startup is the durable, testable signal. Inspecting a live process's loaded modules is fragile and unnecessary.
- **Do not solve wedged-session detection here.** That is the resilience workstream (#1815/#1877). This plan bounds the *consequence* (a starved restart must escalate, not silently expire), not the wedge itself.
- **Do not couple the beacon to Sentry.** Sentry release is external and only visible after an event fires; the updater needs a local, synchronous signal.

## Risks

### Risk 1: Cron-mode false failures during legitimate session draining
**Impact:** A hard "release == HEAD" check right after a cron update would fail every time a session is legitimately mid-flight (the intended deferral), turning normal updates red.
**Mitigation:** The cron path classifies `pending` (fresh flag, process draining) as a warning, not a failure; only a `starved` state (flag older than the pending window, or beacon still stale) escalates. The staleness window is the Open Question to confirm.

### Risk 2: Forced restart interrupts an in-flight session
**Impact:** If the escalation force-restarts, it may kill a running session mid-turn.
**Mitigation:** Escalation is bounded and rare (only after the pending window is exceeded — i.e. the deferred path already failed to converge). Prefer draining once more before forcing; log loudly. This is a deliberate tradeoff: a stale fleet running known-broken code is worse than one interrupted session.

### Risk 3: Beacon staleness / orphaned beacon file
**Impact:** A beacon left by a previous process image could read as "current" and mask a stale process.
**Mitigation:** The beacon stores a timestamp; the verifier cross-checks the beacon's timestamp against the process start time (`ps -o lstart`/`etime`, already read in `get_service_status`). A beacon older than the process start is treated as unknown/stale, not a match.

## Race Conditions

### Race 1: Verify reads the beacon before the restarted process has rewritten it
**Location:** `scripts/update/run.py` verify step vs. `bridge`/`worker` startup beacon write.
**Trigger:** In full mode, `verify_running_release()` runs right after `install_service`; the process may have been bootstrapped but not yet reached its startup beacon write.
**Data prerequisite:** the restarted process must have written its `*_boot_sha` beacon before the verifier reads it.
**State prerequisite:** the beacon's timestamp must be newer than the restart moment for the reading to be trusted.
**Mitigation:** the verify step polls the beacon (bounded, e.g. reuse the existing 20s/30s startup poll windows in Step 5) for a beacon whose timestamp post-dates the restart, mirroring the worker-heartbeat freshness check at `run.py:1381-1400`. A beacon that never freshens within the window → mismatch/escalate (the process failed to come up on new code — exactly what we want to catch).

### Race 2: Restart flag set-then-consumed vs. verify in cron mode
**Location:** `git.set_restart_requested` (run.py:1558) vs. worker `_check_restart_flag` (agent_session_queue.py:1780/2203) vs. verify step.
**Trigger:** verify runs while the worker is between the flag-set and its next idle check.
**Data prerequisite:** the pending/starved classification must not race on the flag file being deleted by `_check_restart_flag` mid-read.
**State prerequisite:** classification reads flag age + beacon age atomically enough to be stable.
**Mitigation:** classification is read-only and tolerant — a missing flag with a fresh beacon == HEAD reads as success; a missing flag with a stale beacon reads as starved (the flag expired without cycling). No write contention is introduced by the verifier.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Running `/update` on the Captain (or any bridge machine) to capture the executable proof for acceptance criterion 3 — the agent's dev machine has no Telegram bridge role, so the release-verification output must be captured on a real bridge machine by the operator. The build produces the verification step and a local/full-mode test; the on-bridge proof run is the human-gated step.
- `[SEPARATE-SLUG]` Fixing the underlying session-wedge that starves the restart (resilience workstream) — not filed under a new issue here because this plan only bounds the *consequence*; if a dedicated wedge issue is desired it should be filed separately. (No `[SEPARATE-SLUG #NNN]` tag claimed since no issue is being asserted — this item is explicitly out of scope with the wedge tracked by the existing resilience issues #1815/#1877.)

Everything else relevant — the beacon, the verify step, the mode-aware verdict, the bridge restart path, and the tests — is in scope for this plan.

## Update System

This bug **is** in the update system, so the change is intrinsically to `/update`:
- `scripts/update/run.py` — new verification step wired into the service-restart path; cron summary surfaces mismatches.
- `scripts/update/service.py` — new `verify_running_release()` + `boot_sha` on status.
- `bridge/telegram_bridge.py` + `worker/__main__.py` — write boot-SHA beacons at startup.
- No new deps to propagate. No `migrations.py` change (beacon files are inert, self-healing, and written on next startup on every machine — no data migration needed). The change propagates to all machines via the normal `/update` git pull; the first post-merge full `/update` restarts the fleet and begins writing beacons.

## Agent Integration

No agent integration required — this is entirely internal to the update system and process startup. No new MCP tool, no `.mcp.json` change, no new bridge-imported code surface. The agent already invokes `/update` via the existing `remote-update.sh` / cron path; that path gains the verification step without a new entry point.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the update-system doc) to describe the boot-SHA beacon and the post-restart release-verification gate, including the cron-mode pending-vs-starved policy.
- [ ] Add/refresh an entry in `docs/features/README.md` index for the release-verification behavior.

### Inline Documentation
- [ ] Docstrings on `verify_running_release()` and the beacon-writer explaining the pending-vs-starved classification and the beacon-freshness cross-check.
- [ ] Comment at the `run.py` verify step explaining why cron mode does not hard-fail on a fresh pending restart.

## Success Criteria

- [ ] Root cause documented (this plan's Recon/Data Flow): cron `/update` never restarts the bridge and its worker restart can be starved+expired, with no release verification. (Acceptance criterion 1.)
- [ ] Bridge and worker write a boot-SHA beacon at startup.
- [ ] `/update` verifies bridge AND worker running release == pulled HEAD after the restart step and exits non-zero on mismatch in full mode; escalates (force-restart or fail) rather than silently passing in cron mode. (Acceptance criterion 2.)
- [ ] The bridge has a cron-mode path to reach new code (flag consumption and/or verification-driven force-restart).
- [ ] Executable proof captured on a bridge machine: `/update` run output showing the release-verification step (SHA match, or a deliberate mismatch producing non-zero exit). (Acceptance criterion 3 — operator-gated per No-Gos.)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `run.py` calls `verify_running_release` and both startup paths call the beacon writer.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (beacon + verify)**
  - Name: `update-verify-builder`
  - Role: Add boot-SHA beacon writes (bridge + worker) and `verify_running_release()`; wire the mode-aware verify step into `run.py`.
  - Agent Type: builder
  - Domain: async/process-lifecycle
  - Resume: true

- **Builder (bridge restart path)**
  - Name: `bridge-restart-builder`
  - Role: Give the bridge a cron-mode restart path (flag consumption idle-gated, or verification-driven force-restart).
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `release-verify-tester`
  - Role: Unit tests for `verify_running_release()` classification, beacon freshness, full-mode hard-fail, cron pending-vs-starved, and startup beacon writes.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `release-verify-validator`
  - Role: Verify acceptance criteria and Verification-table checks.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `release-verify-docs`
  - Role: Update self-healing / update-system docs and index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Boot-SHA beacon writers
- **Task ID**: build-beacon
- **Depends On**: none
- **Validates**: tests/unit/test_update_release_verify.py (create), startup-side-effect tests
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a best-effort beacon writer (SHA + ISO timestamp) writing `data/bridge_boot_sha` and `data/worker_boot_sha`.
- Call it at bridge startup (near `bridge/telegram_bridge.py:2985`) and worker startup (`worker/__main__.py`), swallowing FS errors.

### 2. verify_running_release() + run.py wiring
- **Task ID**: build-verify
- **Depends On**: build-beacon
- **Validates**: tests/unit/test_update_release_verify.py
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `verify_running_release()` to `scripts/update/service.py` with per-process `{running, boot_sha, matches, beacon_age}` and a beacon-vs-process-start freshness cross-check.
- Wire the verify step into `run.py`: full mode → hard-fail on mismatch; cron mode → pending (warn) vs. starved (escalate). Surface mismatches in the cron summary.

### 3. Bridge cron-mode restart path
- **Task ID**: build-bridge-restart
- **Depends On**: build-beacon
- **Validates**: agent/agent_session_queue restart-flag tests
- **Assigned To**: bridge-restart-builder
- **Agent Type**: builder
- **Parallel**: true
- Give the bridge an idle-gated restart-flag consumption path OR have the cron verify step force-restart a bridge whose boot SHA lags HEAD. Keep the worker path unchanged.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-verify, build-bridge-restart
- **Assigned To**: release-verify-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Cover verify classification, beacon freshness, full-mode non-zero exit, cron pending-vs-starved, startup beacon writes, and best-effort failure handling.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-verify, build-bridge-restart, build-tests
- **Assigned To**: release-verify-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the self-healing / update-system feature doc + index; add docstrings and the cron-mode comment.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: release-verify-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification-table checks; confirm acceptance criteria 1 and 2 met and criterion 3 is staged for the operator-gated on-bridge run.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit -x -q -k "release_verify or boot_sha or restart_flag"` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update bridge worker agent` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update bridge worker agent` | exit code 0 |
| run.py calls verifier | `grep -c "verify_running_release" scripts/update/run.py` | output > 0 |
| verifier defined | `grep -c "def verify_running_release" scripts/update/service.py` | output > 0 |
| bridge writes beacon | `grep -rn "bridge_boot_sha" bridge/telegram_bridge.py` | exit code 0 |
| worker writes beacon | `grep -rn "worker_boot_sha" worker/__main__.py` | exit code 0 |
| full-mode failure wired | `grep -n "result.success = False" scripts/update/run.py` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Cron-mode escalation policy:** when the deferred restart is starved (flag stale AND beacon still lags HEAD), should `/update` (a) force a synchronous restart and re-verify, or (b) hard-fail non-zero and leave the process alone for the operator? Force-restart converges the fleet automatically but may interrupt a wedged session; hard-fail is safer but leaves stale code running until a human acts. Recommendation: force-restart with one drain attempt, then hard-fail if still stale.
2. **Pending window duration:** how long may a cron restart legitimately stay pending before it counts as starved? The restart flag's own TTL is 1h (`_RESTART_FLAG_TTL`). Reuse 1h, or a shorter window (e.g. 30 min = the polling-cron interval, so a second cron run would catch it)?
3. **Bridge restart path preference:** idle-gated flag consumption in the bridge (symmetry with the worker) vs. verification-driven force-restart only. Flag consumption is cleaner but adds a new self-restart code path to the bridge; force-restart keeps the bridge dumb. Which does the operator prefer?
