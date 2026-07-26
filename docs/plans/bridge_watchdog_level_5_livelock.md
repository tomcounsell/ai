---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-26
tracking: https://github.com/tomcounsell/ai/issues/2396
last_comment_id:
---

# Bridge Watchdog Level-5 Livelock

## Problem

The bridge watchdog (`monitoring/bridge_watchdog.py`, run every 60s by launchd as `com.valor.bridge-watchdog`) implements a 5-level recovery escalation ladder documented in [`docs/features/bridge-self-healing.md`](../features/bridge-self-healing.md). When the known Telethon update-loop wedge (see "Update-Loop Wedged Detector" below) recurs faster than every ~6 minutes, the ladder gets permanently stuck at level 5 ("alert human") — which takes **no corrective action and sends no alert** — instead of executing the plain restart that the wedge detector's own code explicitly caps it at and that reliably clears the condition.

**Current behavior:**

`check_bridge_health()` in `monitoring/bridge_watchdog.py` computes `recovery_level` from several independent checks. The wedge detector (`assess_update_flow()`) is documented and commented to cap its contribution at level 2 — a `launchctl kickstart` restart, safe because the bridge unconditionally re-initializes Telethon with `catch_up=True` on every connect. But immediately after, a second, reason-blind rule overrides this:

```python
recent_crashes = get_recent_crashes(1800)  # 30 min
if len(recent_crashes) >= 5:
    recovery_level = max(recovery_level, 5)  # Alert human
```

`execute_recovery()`'s level-5 branch only does `logger.critical(...)` and `log_crash("... Recovery exhausted")` — it never calls `restart_bridge()`, and despite the escalation table documenting level 5 as "Alert human via Telegram," no such notification exists anywhere in the codebase (`grep -rn "Recovery exhausted" --include="*.py" .` returns only the `log_crash()` call site).

Because nothing at level 5 clears the underlying Redis `bridge:last_update_received` signal, the wedge re-fires on the very next 60-second tick. The 30-minute crash count never drops back below 5 once it crosses the threshold, so `recovery_level` is pinned at 5 forever — a self-sustaining livelock. Observed live on 2026-07-26: `logs/watchdog.log` shows "Executing recovery level 5" on every single ~60s cycle for 7+ continuous hours, with zero interleaved lower-level restart attempts, and `python monitoring/bridge_watchdog.py --check-only` reporting `Recovery level: 5` and `50 crashes in last 30 minutes` while the bridge process sat wedged and undelivered the whole time. The practical effect was repeated interruption of Telegram-driven SDLC work, since the bridge was never actually recovering.

**Desired outcome:** A wedge-caused crash streak still gets the restart the wedge detector already judged safe — every cycle, regardless of how many times it's recurred in the trailing 30 minutes. If the ladder does reach a state where it wants to escalate beyond that capped action (i.e. the crash-count signal fires), it delivers a real, deduplicated human-visible notification instead of a silent log line, matching what the documentation already promises.

## Freshness Check

**Baseline commit:** `2b5a0b29c` (`git rev-parse HEAD` at plan time — the same commit the livelock was observed live against; no commits landed on `main` between issue filing and this plan)
**Issue filed at:** 2026-07-26 (same session, within the last hour of this plan)
**Disposition:** Unchanged

Per the skill's skip condition (issue filed within the last hour, no commits since), the full file:line re-verification and sibling issue/PR re-check are skipped. The diagnosis in issue #2396 was gathered by direct, live inspection of `monitoring/bridge_watchdog.py`, `logs/watchdog.log`, and `monitoring/crash_tracker.py` in the same session that wrote this plan — there is no drift window to account for.

## Prior Art

- **#1408** (closed): "Messages permanently lost in Telethon update gap." Diagnosed the original silent Telethon delivery gap and fixed the catchup-scan/reconciler dead zone (`docs/plans/completed/catchup-dead-zone-reconciler-lookback.md`). Established that Telethon can silently stop delivering events while remaining connected — the same underlying Telethon behavior this issue is downstream of. Did not touch watchdog escalation logic.
- **#1712 / PR #1723**: "feat(#1712): bridge stale-update-stream detector + watchdog auto-recovery." Added `assess_update_flow()` and the level-2 cap this issue is about — the cap is correctly implemented in that function; the defect is a *different*, pre-existing rule elsewhere in `check_bridge_health()` that overrides it. This plan does not revisit #1712's detection logic, only the escalation interaction downstream of it.
- **#1821** ("Resilience: out-of-domain recovery + per-tool budget backstop"): built the *worker*-side loop-beacon liveness/reclaim system (`monitoring/session_watchdog.py::check_worker_liveness_and_slots`), a structurally similar but functionally separate livelock-avoidance mechanism for a different failure domain (worker process vs. bridge process). Useful precedent for the "detect but defer to a different owner, never silently no-op" pattern, though that system correctly defers instead of silently doing nothing.

No prior attempt to fix this specific level-5 interaction was found — this is the first fix attempt, so no "Why Previous Fixes Failed" section is needed.

## Research

No relevant external findings — this is purely internal escalation-logic control flow in `monitoring/bridge_watchdog.py`; no external libraries, APIs, or ecosystem patterns are involved. (Telethon itself is referenced but not touched by this fix.)

## Data Flow

1. **Entry point**: launchd (`com.valor.bridge-watchdog`, `StartInterval=60`) invokes `python monitoring/bridge_watchdog.py` once per tick — a fresh process each time, no in-memory state carries over between ticks.
2. **`run_health_check()`**: checks hibernation state and recovery-lock/planned-restart suppression, then calls `check_bridge_health()`.
3. **`check_bridge_health()`**: runs five independent checks (process alive, logs fresh, crash pattern, zombies, `assess_update_flow()` wedge detection), each contributing to a single `recovery_level` int via `max()`. The crash-count check (`get_recent_crashes(1800) >= 5`) is the last contributor and — critically — is reason-blind: it does not know *why* the prior crashes happened, only that there were enough of them.
4. **`execute_recovery(level, issues)`**: dispatches on the final `recovery_level` alone. Levels 1-4 take a corrective action (restart, with escalating cleanup). Level 5 takes no corrective action — only logs.
5. **Output**: for levels 1-4, `restart_bridge()` calls `launchctl kickstart -k gui/{uid}/com.valor.bridge`, which should reset the Telethon connection and clear the wedge (verified indirectly: the bridge unconditionally passes `catch_up=True` on connect). For level 5 today, the only output is a log line in `logs/watchdog.log` — nothing reaches a human or the bridge process.

This trace is what shows the fix must happen in `check_bridge_health()` / `execute_recovery()` specifically: the wedge detector (step 3, `assess_update_flow()`) already does the right thing at its own layer; the defect is entirely in how the *aggregate* `recovery_level` is computed and dispatched afterward.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `HealthStatus` (dataclass in `monitoring/bridge_watchdog.py`) gains a field to separate "action level" from "alert needed" (exact naming decided during build — see Technical Approach). Existing fields (`recovery_level`, `healthy`, `issues`, etc.) are preserved for backward compatibility with `--check-only` output and existing test fixtures.
- **Coupling**: none added — this stays entirely within `monitoring/bridge_watchdog.py`. The human-alert delivery reuses the existing enqueue-an-AgentSession pattern already used by `send_hibernation_notification()` in `agent/sustainability.py`, so no new coupling to the Telegram send path is introduced beyond what already exists.
- **Data ownership**: unchanged — the watchdog remains the sole owner of `recovery_level` semantics and the crash-count read from `monitoring/crash_tracker.py`.
- **Reversibility**: trivially reversible — a single-file logic change with full test coverage; no data migration, no schema change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (standard SDLC review stage)

This is a bounded, single-file control-flow fix with an already-narrow blast radius (confirmed: only `monitoring/bridge_watchdog.py` is imported by `tests/unit/test_bridge_watchdog.py` and `tests/integration/test_update_loop_wedge_recovery.py` — no other module imports from it). No new dependencies, no new external surface, no cross-service coordination.

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies existing, already-deployed infrastructure code with no new API keys, services, or config.

## Solution

### Key Elements

- **Decoupled action level from alert signal**: `check_bridge_health()` computes the corrective-action level (1-4) exactly as today, from the individual checks, *without* the recent-crash-count rule folded in. A separate boolean (e.g. `human_alert_needed`) captures whether the crash-count threshold (`>= 5 in 30 min`) has been crossed.
- **Always-attempt-the-capped-action semantics**: `execute_recovery()` always executes the real action level (1-4) it's given — it is never handed a no-op level 5 in place of the actual, already-judged-safe action. This is what breaks the livelock: a wedge that recurs 50 times in 30 minutes still gets 50 actual restart attempts, each of which has a real chance of clearing the underlying Telethon state.
- **Deduplicated human alert layered on top**: when `human_alert_needed` is true, a real notification fires — reusing the existing `send_hibernation_notification`-style pattern (enqueue a lightweight AgentSession that sends a Telegram message) — gated by a cooldown (Redis key or `data/` sentinel file with a TTL, e.g. 30-60 minutes) so a persistent crash storm sends one alert per window, not one every 60 seconds.
- **Preserved safety gates**: level 4 (auto-revert) remains gated on `AUTO_REVERT_ENABLED_FILE` exactly as today. The wedge detector's own contribution to the action level still never exceeds 2 on its own — the crash-count signal only ever adds an *alert*, never elevates the *action* level it didn't independently earn.

### Flow

Watchdog tick (launchd, every 60s) → `check_bridge_health()` computes (action_level 1-4, human_alert_needed bool) → `execute_recovery(action_level, issues)` always takes the real corrective action for that level → if `human_alert_needed` and cooldown window open → enqueue Telegram alert AgentSession, set cooldown key → next tick, if the action actually cleared the wedge, `recent_crashes` count decays out of the 30-minute window and `human_alert_needed` goes false again.

### Technical Approach

- Rename/restructure so the crash-count check no longer calls `recovery_level = max(recovery_level, 5)`. Instead it sets a separate signal, e.g. `HealthStatus.human_alert_needed: bool` (or reuse `recovery_level` as the action level and add `alert_level` — implementer's choice; keep whichever reads most clearly against the existing 5-level doc table).
- `execute_recovery()` drops the level-5 no-op branch as a *dispatch target* — level 5 is no longer a distinct action level. The alert becomes a side effect triggered by `human_alert_needed`, checked independently of (and in addition to) whichever of levels 1-4 actually runs.
- Implement the alert as a new small function (e.g. `_alert_human_of_crash_storm(issues: list[str]) -> None`) modeled directly on `send_hibernation_notification()` in `agent/sustainability.py` — enqueue an AgentSession with a pre-composed "send this exact Telegram message" instruction to the same target chat (`Eng: Valor`, per the existing pattern) rather than building a new direct-send path. Wrap in try/except, never raise (matches the fail-quiet contract every other function in this module already follows).
- Cooldown for the alert: a simple `data/bridge-watchdog-alert-cooldown` sentinel file (mtime-based, mirroring `RECOVERY_LOCK`'s existing pattern in the same module) or a Redis key with `SET NX EX` (mirroring the pattern in `monitoring/session_watchdog.py::_inject_watchdog_steer`) — either is consistent with existing conventions in this codebase; implementer picks whichever is simpler to test.
- Update `docs/features/bridge-self-healing.md`'s escalation table and the "Recovery" paragraph under "3a. Update-Loop Wedged Detector" to describe the corrected behavior (action level always executes; alert is a separate, deduplicated side channel — not a 6th action level).
- Update `--check-only` CLI output (`main()` in `monitoring/bridge_watchdog.py`) to print the alert-needed state alongside the action level, so `python monitoring/bridge_watchdog.py --check-only` remains a complete, accurate diagnostic.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new alert function must follow the existing fail-quiet contract in this file (every other recovery helper — `restart_bridge()`, `revert_last_commit()`, `kill_zombie_processes()` — catches broadly and logs rather than raising). Add a test asserting an exception inside the alert path (e.g. `AgentSession` creation raising) is caught and logged, and does NOT prevent `execute_recovery()` from completing the actual corrective action for that tick.
- [ ] No other exception handlers are newly introduced in `check_bridge_health()`/`execute_recovery()` beyond the existing ones already covered by current tests.

### Empty/Invalid Input Handling
- [ ] Document behavior when `get_recent_crashes()` returns an empty list (no alert, action level unaffected — already covered by existing tests, just re-verify post-change).
- [ ] Test the cooldown-not-yet-open case (alert requested twice within the cooldown window → second call is a no-op, no duplicate AgentSession/Telegram message).

### Error State Rendering
- [ ] `--check-only` output must clearly render both the action level taken/would-take and whether a human alert fired or is on cooldown — verify by running `python monitoring/bridge_watchdog.py --check-only` against a fixture-forced crash-storm state in an integration test and asserting the printed text.

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py::TestCrashDetectionOnBridgeDeath::test_log_crash_failure_does_not_break_health_check` — UPDATE: still asserts `status.recovery_level >= 1`; confirm this remains true under the new field split (process-not-running still contributes action level 1 regardless of the alert-signal refactor).
- [ ] `tests/unit/test_bridge_watchdog.py::TestHealthStatus` (lines ~336-374) — UPDATE: `HealthStatus` fixture construction sites need the new field (`human_alert_needed` or equivalent) added with a sensible default so existing fixtures don't break on missing required args.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckOnlyOutput` (line ~439) — UPDATE: extend assertions to cover the new alert-state line in `--check-only` output.
- [ ] **New test** (no existing coverage found via `grep -n "get_recent_crashes\|recovery_level.*5" tests/unit/test_bridge_watchdog.py`): a regression test that reproduces the exact livelock — simulate `get_recent_crashes()` returning >= 5 wedge-reason crashes while `assess_update_flow()` reports wedged (action level 2) — and assert `execute_recovery()` is invoked with the real action level (2, restart attempted) rather than a level that takes no action, AND that the alert function is invoked exactly once (not on every tick) across repeated calls within the cooldown window.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` — no changes expected; this file tests `assess_update_flow()` directly, which is unmodified by this plan. Re-run as a regression check only.

No other test files reference `check_bridge_health`, `execute_recovery`, or `recovery_level` (confirmed via `grep -rln "check_bridge_health\|execute_recovery\|recovery_level" tests/`), so the blast radius on the test suite is fully contained to `tests/unit/test_bridge_watchdog.py`.

## Rabbit Holes

- **Do not migrate off Telethon.** The archived-library wedge is an accepted, already-mitigated condition (#1408); this plan is strictly about the escalation ladder failing to apply its own mitigation, not about eliminating the wedge's root cause.
- **Do not redesign the full 5-level ladder or its doc table.** Levels 1-4 are working as documented; only the level-5 dispatch and its interaction with the crash-count check need to change. Resist the temptation to generalize into a full alerting/observability subsystem.
- **Do not build a new Telegram-send code path.** Reuse `send_hibernation_notification()`'s established pattern (enqueue an AgentSession with instructions) rather than adding a second, parallel way to originate outbound Telegram messages from a monitoring script.

## Risks

### Risk 1: Restart storm if the alert-decoupling accidentally makes restarts *more* aggressive than before
**Impact:** If the fix is implemented sloppily (e.g. by just deleting the crash-count check entirely rather than decoupling it), a genuinely broken bridge (crashing for reasons *other* than the capped, known-safe wedge) could restart every 60 seconds indefinitely with no backstop at all, worse than today's do-nothing state.
**Mitigation:** The fix must preserve escalation gating for non-wedge reasons — level 4 (auto-revert) stays gated on `AUTO_REVERT_ENABLED_FILE`, and levels 1-3 already have their own natural ceilings (each is a single restart attempt, not a tightening loop). The new regression test specifically asserts the *action* level stays exactly what the individual checks computed (never silently escalated by the crash-count rule) — this is the acceptance bar for "decoupled, not deleted."

### Risk 2: Alert spam or missed alerts if the cooldown key is implemented incorrectly
**Impact:** A too-short cooldown re-spams a human every few minutes during a real incident (noise, alert fatigue); a too-long or broken cooldown means a genuinely stuck bridge report is delivered once and then silently suppressed for the rest of the incident.
**Mitigation:** Use an atomic `SET NX EX`-style check-and-set (matching the existing pattern in `monitoring/session_watchdog.py::_inject_watchdog_steer`) rather than a read-then-write race. Test both the "still on cooldown" and "cooldown expired, re-alert" paths explicitly.

### Risk 3: The alert notification itself depends on infrastructure that may be unhealthy during the exact incident it's meant to report
**Impact:** `send_hibernation_notification()`-style alerts work by enqueueing an AgentSession for the *worker* process to execute and send via the bridge's outbound Telegram path. If the bridge is wedged only on the *inbound* side (the actual failure mode here — Telethon stops delivering `NewMessage` events, not necessarily stops sending), outbound delivery should still work. But if recovery level 1 (process not running) is what's escalating, there's no bridge process alive to relay the message at all.
**Mitigation:** Document this limitation explicitly rather than solving it in this plan — the alert is a best-effort improvement over today's complete silence, not a guaranteed-delivery guarantee. `log_crash()` and the `logs/watchdog.log` critical entry remain the durable, always-available record regardless of whether the Telegram alert itself gets through.

## Race Conditions

No race conditions identified beyond what already exists in this file. `monitoring/bridge_watchdog.py` runs as a single, non-concurrent process per launchd tick (`StartInterval=60` spawns one process at a time; the existing `RECOVERY_LOCK` file already guards against overlapping recovery attempts if a tick runs long). The new alert-cooldown check must use an atomic primitive (Redis `SET NX EX` or an equivalent file-based check-then-write with `O_EXCL`) to avoid a theoretical double-alert if two watchdog processes somehow overlap (e.g. a manually-triggered `--check-only` run coinciding with the scheduled tick) — this is the same class of hazard the existing `RECOVERY_LOCK` file already defends against, so the implementation should follow that established pattern rather than inventing a new one.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1408] Migrating away from Telethon or otherwise eliminating the underlying update-loop wedge at its source — already tracked and partially mitigated by #1408; this plan only fixes the escalation ladder's response to the wedge recurring.
- Nothing else deferred — every item needed to break the level-5 livelock and restore the documented alert behavior is in scope for this plan.

## Update System

No update system changes required — this is a single-file logic fix to `monitoring/bridge_watchdog.py`, deployed the same way as any other code change (`git pull` + existing launchd service definitions already reference this same script path and interval; no new config, no new dependencies, no migration for existing installations).

## Agent Integration

No new tool/MCP surface is needed. The human-alert path reuses the existing pattern already wired end-to-end: `send_hibernation_notification()`-style AgentSession enqueue → worker executes the session → session sends a Telegram message via the bridge's existing send path. This plan adds one new call site following that same established pattern; it does not add a new CLI entry point, MCP tool, or bridge-internal import surface. Integration test: the new regression test in `tests/unit/test_bridge_watchdog.py` should mock the `AgentSession`-enqueue call (matching how `send_hibernation_notification` itself is tested, if a test for it exists — check `tests/` for `test_sustainability.py` or similar and mirror its mocking approach) rather than actually creating a session or sending Telegram traffic in unit tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md`: correct the 5-level escalation table (level 5 is no longer a distinct "do nothing but log" action; describe it as a deduplicated alert layered on top of whatever action level 1-4 actually executes) and the "Recovery" paragraph under "3a. Update-Loop Wedged Detector" to reflect that the level-2 cap is now actually honored end-to-end (previously true only in isolation, not in the aggregate dispatch).
- [ ] No new entry needed in `docs/features/README.md` — this is a correction to existing documented behavior, not a new feature.

### Inline Documentation
- [ ] Update or remove the now-partially-inaccurate code comment "Level cap: the wedge detector contributes at most level 2 ... must never push recovery_level to 4" in `assess_update_flow()` if the field split changes what "recovery_level" means at that call site — replace with an accurate description of the corrected two-signal (action level / alert flag) design.
- [ ] Add a docstring/comment on the new alert function explaining the cooldown rationale (mirroring the level of detail in `_inject_watchdog_steer()`'s docstring in `monitoring/session_watchdog.py`, which is the closest existing precedent in this codebase).

Both sub-items above are required — this plan corrects a doc/code drift, so both the feature doc and inline comments must change.

## Success Criteria

- [ ] A simulated wedge-caused crash streak (>= 5 crashes in 30 min, all `bridge_update_loop_wedged`) results in `execute_recovery()` being called with the real action level (2) every tick, not a no-op level.
- [ ] A human-visible alert fires exactly once per cooldown window under a sustained crash storm, not once per 60-second tick.
- [ ] `docs/features/bridge-self-healing.md` accurately describes the corrected behavior.
- [ ] Level 4 (auto-revert) still requires `AUTO_REVERT_ENABLED_FILE` and is unreachable via the wedge-detector or crash-count paths alone.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No agent integration section item applies beyond reusing the existing AgentSession-enqueue pattern — grep confirms the new alert function calls into the same `AgentSession`/enqueue pattern `send_hibernation_notification()` uses, not a new send path.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (watchdog-escalation)**
  - Name: watchdog-builder
  - Role: Implement the action-level/alert-signal split in `monitoring/bridge_watchdog.py`, the new alert function, and the cooldown mechanism
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-escalation)**
  - Name: watchdog-validator
  - Role: Verify the livelock is actually broken (new regression test passes, existing tests updated correctly, no restart-storm regression)
  - Agent Type: validator
  - Resume: true

- **Documentarian (watchdog-docs)**
  - Name: watchdog-documentarian
  - Role: Update `docs/features/bridge-self-healing.md` and inline comments to match corrected behavior
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Split action level from alert signal
- **Task ID**: build-watchdog-escalation-split
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_watchdog.py (updated fixtures + new regression test)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- In `monitoring/bridge_watchdog.py`, remove the `recovery_level = max(recovery_level, 5)` override from the recent-crash-count check in `check_bridge_health()`; replace with a separate `human_alert_needed` (or equivalently named) signal on `HealthStatus`.
- Update `execute_recovery()` so the level-5 branch is no longer a dispatch target for the action level; the real action level (1-4, or 0/healthy) always executes as computed by the individual checks.
- Update `--check-only` output in `main()` to print the alert-needed state alongside the action level.

### 2. Implement deduplicated human alert
- **Task ID**: build-watchdog-alert
- **Depends On**: build-watchdog-escalation-split
- **Validates**: tests/unit/test_bridge_watchdog.py (new alert + cooldown tests)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_alert_human_of_crash_storm(issues: list[str]) -> None` modeled on `send_hibernation_notification()` in `agent/sustainability.py`; fail-quiet, never raises.
- Implement an atomic cooldown check (Redis `SET NX EX`, mirroring `monitoring/session_watchdog.py::_inject_watchdog_steer`, or an equivalent file-based check with the same atomicity guarantee).
- Wire the alert call into `run_health_check()` so it fires whenever `human_alert_needed` is true and the cooldown slot is open, independent of which action level ran that tick.

### 3. Update existing tests + add regression coverage
- **Task ID**: build-watchdog-tests
- **Depends On**: build-watchdog-alert
- **Validates**: tests/unit/test_bridge_watchdog.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `HealthStatus` fixture construction sites across `TestHealthStatus`, `TestCheckOnlyOutput`, and `TestCrashDetectionOnBridgeDeath` for the new field.
- Add the new regression test reproducing the exact livelock scenario (>= 5 wedge-reason crashes → action level still executes; alert fires once, not every tick).
- Add the cooldown-not-yet-open / cooldown-expired test pair.

### 4. Validate the fix
- **Task ID**: validate-watchdog-escalation
- **Depends On**: build-watchdog-tests
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_watchdog.py tests/integration/test_update_loop_wedge_recovery.py -x -q` and confirm all pass.
- Manually walk through the new regression test's assertions against the Risk 1 mitigation (action level is never silently escalated by the crash-count rule).
- Confirm `docs/features/bridge-self-healing.md` accurately reflects the new code (read both side by side).

### 5. Documentation
- **Task ID**: document-watchdog-escalation
- **Depends On**: validate-watchdog-escalation
- **Assigned To**: watchdog-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md`'s escalation table and "3a. Update-Loop Wedged Detector" section.
- Update the now-inaccurate inline comment in `assess_update_flow()`.
- Add a docstring on the new alert function.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-watchdog-escalation
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Confirm all Success Criteria are met.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_watchdog.py tests/integration/test_update_loop_wedge_recovery.py -x -q` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/bridge_watchdog.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/bridge_watchdog.py` | exit code 0 |
| Livelock regression test exists | `grep -c "def test_.*livelock\|def test_.*action_level.*executes\|def test_.*crash_storm" tests/unit/test_bridge_watchdog.py` | output > 0 |
| Level 4 still gated | `grep -n "AUTO_REVERT_ENABLED_FILE" monitoring/bridge_watchdog.py` | output contains AUTO_REVERT_ENABLED_FILE |
| No bare level-5 no-op dispatch remains | `grep -n "elif level == 5:" monitoring/bridge_watchdog.py` | exit code != 0 |
| Docs updated | `grep -c "human_alert_needed\|deduplicated alert\|alert.*layered" docs/features/bridge-self-healing.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Naming: should the new `HealthStatus` field be `human_alert_needed: bool`, or should `recovery_level` itself be repurposed to mean "action level" (0-4 only) with a parallel `alert_level` int for symmetry with the existing naming? Either works; pick whichever reads more clearly against the existing `--check-only` output consumers.
2. Cooldown storage: Redis `SET NX EX` (consistent with `session_watchdog.py`) vs. a `data/`-directory sentinel file (consistent with `RECOVERY_LOCK` in the same module, `bridge_watchdog.py`)? Both are established patterns in this codebase; no strong reason to prefer one, but pick one and don't mix.
3. Cooldown duration: is 30 minutes (matching the crash-count window itself) the right default, or should it be longer (e.g. 60 min) to reduce alert fatigue during a multi-hour incident like the one observed? Default assumption in this plan: 30 minutes, adjustable via an env var following the existing `WATCHDOG_*` / `TOKEN_ALERT_COOLDOWN`-style convention if the builder judges a knob is warranted.
