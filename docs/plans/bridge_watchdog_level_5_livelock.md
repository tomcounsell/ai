---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-26
tracking: https://github.com/tomcounsell/ai/issues/2396
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-27T03:33:48Z
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
- **Interface changes**: `HealthStatus` (dataclass in `monitoring/bridge_watchdog.py`) gains two new fields with defaults — `human_alert_needed: bool = False` and `restart_circuit_open: bool = False` — separating "action level" from "alert needed" and from "suppress restart (non-wedge storm)". Both carry defaults so existing fixture construction sites do not break. Existing fields (`recovery_level`, `healthy`, `issues`, etc.) are preserved for backward compatibility with `--check-only` output and existing test fixtures. `execute_recovery()`'s signature is unchanged, but it no longer accepts level 5.
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

**Why the two changes ship together, not split (C3):** removing the level-5 no-op (the livelock fix) and adding the human alert are not independently shippable. Landing the removal alone would delete the *only* signal a crash storm currently produces — the `logger.critical` + `log_crash("Recovery exhausted")` — leaving genuine multi-hour incidents fully silent, which is strictly worse than today. Landing the alert alone leaves the livelock in place. Any split produces an intermediate `main` state worse than the status quo, so the changes are one atomic unit. They are still small: three helpers (`_recovery_exhausted`, `_alert_human_of_crash_storm`, `_alert_cooldown_open`), two new `HealthStatus` fields, and edits to three existing functions, all in one file.

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies existing, already-deployed infrastructure code with no new API keys, services, or config.

## Solution

### Key Elements

- **Decoupled action level from alert signal**: `check_bridge_health()` computes the corrective-action level (1-4) exactly as today, from the individual checks, *without* the recent-crash-count rule folded in. A separate boolean (`human_alert_needed`) captures whether the crash-count threshold (`>= 5 in 30 min`) has been crossed.
- **Always-attempt-the-capped-action semantics for the wedge**: `execute_recovery()` always executes the real action level (1-4) it's given — it is never handed a no-op level 5 in place of the actual, already-judged-safe action. This is what breaks the livelock: a *wedge-caused* storm that recurs 50 times in 30 minutes still gets 50 actual restart attempts, each of which has a real chance of clearing the underlying Telethon state.
- **Reason-aware restart circuit breaker for non-wedge storms (C2)**: today the crash-count → level-5 no-op rule is the *only* thing that throttles restarts when the bridge is crash-looping for a non-wedge reason (a code bug that crashes on boot). Simply deleting it would let a genuinely-broken bridge restart every 60s forever (Risk 1). So the crash-count rule is not deleted — it is made *reason-aware*. `get_recent_crashes()` returns `CrashEvent`s carrying a `.reason`; when a crash storm is **wedge-dominated** the restart always runs (livelock fix), and when it is **non-wedge-dominated** a `restart_circuit_open` signal suppresses the restart for that tick (preserving today's "stop thrashing a broken bridge" backstop) while still firing the alert. This directly replaces the throttle the old no-op provided rather than dropping it.
- **Deduplicated human alert layered on top**: when `human_alert_needed` is true, a real notification fires — reusing the existing `send_hibernation_notification`-style pattern (enqueue a lightweight AgentSession that sends a Telegram message) — gated by a file-sentinel cooldown (see Technical Approach) so a persistent crash storm sends one alert per window, not one every 60 seconds. The same alert primitive is reused by the level-4 "recovery exhausted" fallback (B1), so both silent-failure paths now surface a human-visible alert.
- **Preserved safety gates**: level 4 (auto-revert) remains gated on `AUTO_REVERT_ENABLED_FILE` exactly as today. The wedge detector's own contribution to the action level still never exceeds 2 on its own — the crash-count signal only ever adds an *alert* (and, for non-wedge storms, *removes* a restart via the circuit breaker); it never elevates the *action* level it didn't independently earn.

### Flow

Watchdog tick (launchd, every 60s) → `check_bridge_health()` computes (`recovery_level` 0-4, `human_alert_needed` bool, `restart_circuit_open` bool) → in `run_health_check()`: if `human_alert_needed` → `_alert_human_of_crash_storm(issues)` (self-gated by the cooldown sentinel — fires at most once per window) → then, if `restart_circuit_open` (non-wedge storm), log critical + `return False` *without* restarting (throttle preserved); otherwise `execute_recovery(recovery_level, issues)` takes the real corrective action → next tick, if the action actually cleared the wedge, `recent_crashes` decays out of the 30-minute window and both `human_alert_needed` and `restart_circuit_open` go false again. Inside `execute_recovery()`, the level-4 auto-revert fallback (auto-revert disabled, or revert failed) routes to `_recovery_exhausted(issues)` — which logs critical, records `log_crash("Recovery exhausted")`, fires the same deduplicated alert, and returns False (B1) — instead of the deleted level-5 dispatch branch.

### Technical Approach

**Signal split in `check_bridge_health()`.**
- The crash-count check no longer calls `recovery_level = max(recovery_level, 5)` (current line 513). Instead it sets `HealthStatus.human_alert_needed: bool` (decision, OQ1). `recovery_level` retains its existing 0-4 action-level meaning; the crash-count rule never touches `recovery_level`. This keeps the existing `--check-only` output and test fixtures reading naturally against the documented ladder.
- The same check computes `HealthStatus.restart_circuit_open: bool` (C2). `get_recent_crashes(1800)` returns `CrashEvent` objects with a `.reason` field. Compute `wedge_count = sum(1 for c in recent_crashes if c.reason == "bridge_update_loop_wedged")`. When `len(recent_crashes) >= CRASH_STORM_THRESHOLD` and the storm is **not** wedge-dominated (`wedge_count < len(recent_crashes) * WEDGE_DOMINANCE_FRACTION`), set `restart_circuit_open = True`. `CRASH_STORM_THRESHOLD` (default 5, the existing hard-coded threshold promoted to a named constant) and `WEDGE_DOMINANCE_FRACTION` (default 0.5) are module-level constants with a grain-of-salt comment marking them provisional/tunable. A wedge-dominated storm leaves `restart_circuit_open` false so the restart always runs — this is the livelock fix.

**`execute_recovery()` — remove the level-5 dispatch and fix the orphaned call sites (B1).**
- Extract the current level-5 branch body (lines 737-746: `logger.critical("… levels 1-4 exhausted …")` + `log_crash("… Recovery exhausted")` + `return False`) into a new helper `_recovery_exhausted(issues: list[str]) -> bool`. The helper keeps that exact critical logging and crash-log call, additionally fires the deduplicated human alert (`_alert_human_of_crash_storm(issues)`), and returns `False`.
- Delete the `elif level == 5:` branch. **Both** internal call sites inside the level-4 branch that currently do `return execute_recovery(5, issues)` (line 720, auto-revert-not-enabled; and line 735, revert-failed) become `return _recovery_exhausted(issues)`. This is mandatory and lands in the same task: without it, deleting the level-5 branch would drop both level-4 fallbacks into a bare `return False`, silently losing the critical-failure logging and crash record. The level-4 auto-revert gate on `AUTO_REVERT_ENABLED_FILE` is unchanged.
- `execute_recovery()` is otherwise unchanged: levels 1-4 execute exactly as today. It no longer accepts or dispatches level 5.

**Alert wiring in `run_health_check()`.**
- After `check_bridge_health()`: if `status.human_alert_needed`, call `_alert_human_of_crash_storm(status.issues)` (the cooldown gate lives inside that function, so callers never double-fire — see below). Then, if `status.restart_circuit_open`, log a critical "non-wedge crash storm — restart circuit open, skipping restart to avoid thrash" line and `return False` *without* calling `execute_recovery()`. Otherwise call `execute_recovery(status.recovery_level, status.issues)` as today.

**Alert function (`_alert_human_of_crash_storm(issues: list[str]) -> None`).**
- Modeled directly on `send_hibernation_notification()` in `agent/sustainability.py` — enqueue an AgentSession with a pre-composed "send this exact Telegram message" instruction to the same target chat (`Eng: Valor`, per the existing pattern) rather than building a new direct-send path. Wrapped in try/except, never raises (matches the fail-quiet contract every other function in this module follows).
- **Cooldown gate lives inside this function**, so every caller (the `human_alert_needed` path in `run_health_check()` and `_recovery_exhausted()` inside `execute_recovery()`) is deduplicated by one shared window — whichever fires first in a tick stamps the sentinel; the other finds it closed and returns immediately.

**Cooldown mechanism — file sentinel with create-or-refresh (OQ2, C1).**
- A `data/bridge-watchdog-alert-cooldown` sentinel file, mtime-based, in the same `DATA_DIR` as `RECOVERY_LOCK`. Chosen over Redis to stay consistent with the module's own file-locking convention and avoid adding a Redis dependency to the alert path.
- **`O_EXCL` is explicitly NOT used** (C1). `O_EXCL` fails when the file already exists, which is incompatible with an mtime-*expiry* sentinel that must persist across windows and be reusable. Instead the gate is a read-mtime-then-refresh-in-place primitive, `_alert_cooldown_open() -> bool`:
  1. `try: age = time.time() - COOLDOWN_FILE.stat().st_mtime` — on `FileNotFoundError` the window is open (never alerted, or file removed).
  2. If the file exists and `age < WATCHDOG_ALERT_COOLDOWN_SECONDS` → return `False` (still on cooldown, no alert).
  3. Otherwise the window is open: stamp the sentinel via `COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)` then `COOLDOWN_FILE.touch()` (creates the file if absent, refreshes its mtime to "now" if present — this is the stale-refresh step that `O_EXCL` cannot express), and return `True`.
- Cooldown duration defaults to 30 minutes — matching the crash-count window itself (decision, OQ3) — exposed as an env-overridable constant `WATCHDOG_ALERT_COOLDOWN_SECONDS` following the existing `WATCHDOG_*` convention, so alert fatigue during a multi-hour incident can be tuned without a code change.
- Update `docs/features/bridge-self-healing.md`'s escalation table and the "Recovery" paragraph under "3a. Update-Loop Wedged Detector" to describe the corrected behavior (action level always executes; alert is a separate, deduplicated side channel — not a 6th action level).
- Update `--check-only` CLI output (`main()` in `monitoring/bridge_watchdog.py`, currently around lines 925-946) to print the new signals alongside the existing `Recovery level:` line: `Human alert needed: <bool>`, `Alert on cooldown: <bool>` (read via a non-stamping variant / direct mtime check so `--check-only` stays read-only and does NOT consume the cooldown window), and `Restart circuit open: <bool>`. This keeps `python monitoring/bridge_watchdog.py --check-only` a complete, accurate diagnostic and is the surface the C4 live post-deploy check reads.

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
- [ ] `tests/unit/test_bridge_watchdog.py::TestHealthStatus` (lines ~336-374) — UPDATE: the two new `HealthStatus` fields (`human_alert_needed`, `restart_circuit_open`) carry defaults (`= False`), so existing fixtures keep working; add assertions covering the defaults and the set-true cases.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckOnlyOutput` (line ~439) — UPDATE: extend assertions to cover the new alert-state line in `--check-only` output.
- [ ] **New test — livelock regression** (no existing coverage found via `grep -n "get_recent_crashes\|recovery_level.*5" tests/unit/test_bridge_watchdog.py`): simulate `get_recent_crashes()` returning >= 5 wedge-reason crashes while `assess_update_flow()` reports wedged (action level 2) — assert `execute_recovery()` is invoked with the real action level (2, restart attempted), `restart_circuit_open` is False, and the alert function is invoked exactly once across repeated calls within the cooldown window.
- [ ] **New test — non-wedge circuit breaker (C2)**: `get_recent_crashes()` returns >= 5 crashes with non-wedge reasons → `restart_circuit_open` is True, `run_health_check()` does NOT call `execute_recovery()` for a restart, and the alert still fires. Confirms today's non-wedge restart throttle is preserved.
- [ ] **New test — level-4 recovery-exhausted fallback (B1)**: with a crash pattern detected and `AUTO_REVERT_ENABLED_FILE` absent (and separately, revert-failed), assert `execute_recovery(4, …)` routes through `_recovery_exhausted()` — critical logged, `log_crash("Recovery exhausted")` recorded, alert fired, returns False — not a bare `return False`.
- [ ] `tests/integration/test_update_loop_wedge_recovery.py` — no changes expected; this file tests `assess_update_flow()` directly, which is unmodified by this plan. Re-run as a regression check only.

No other test files reference `check_bridge_health`, `execute_recovery`, or `recovery_level` (confirmed via `grep -rln "check_bridge_health\|execute_recovery\|recovery_level" tests/`), so the blast radius on the test suite is fully contained to `tests/unit/test_bridge_watchdog.py`.

## Rabbit Holes

- **Do not migrate off Telethon.** The archived-library wedge is an accepted, already-mitigated condition (#1408); this plan is strictly about the escalation ladder failing to apply its own mitigation, not about eliminating the wedge's root cause.
- **Do not redesign the full 5-level ladder or its doc table.** Levels 1-4 are working as documented; only the level-5 dispatch and its interaction with the crash-count check need to change. Resist the temptation to generalize into a full alerting/observability subsystem.
- **Do not build a new Telegram-send code path.** Reuse `send_hibernation_notification()`'s established pattern (enqueue an AgentSession with instructions) rather than adding a second, parallel way to originate outbound Telegram messages from a monitoring script.

## Risks

### Risk 1: Restart storm if the alert-decoupling accidentally makes restarts *more* aggressive than before
**Impact:** If the fix is implemented sloppily (e.g. by just deleting the crash-count check entirely rather than decoupling it), a genuinely broken bridge (crashing for reasons *other* than the capped, known-safe wedge) could restart every 60 seconds indefinitely with no backstop at all, worse than today's do-nothing state.
**Mitigation:** The fix must preserve escalation gating for non-wedge reasons — level 4 (auto-revert) stays gated on `AUTO_REVERT_ENABLED_FILE`, and levels 1-3 already have their own natural ceilings (each is a single restart attempt, not a tightening loop). Crucially, the crash-count rule is **not deleted** but made reason-aware (C2): a non-wedge-dominated crash storm sets `restart_circuit_open`, which suppresses the restart entirely for that tick — this replaces the throttle the old level-5 no-op provided, so a code-bug crash loop no longer restarts every 60s. The new regression tests assert (a) a wedge storm still restarts every tick, and (b) a non-wedge storm opens the circuit (no restart) while still alerting — this pair is the acceptance bar for "decoupled and reason-aware, not deleted."

### Risk 2: Alert spam or missed alerts if the cooldown key is implemented incorrectly
**Impact:** A too-short cooldown re-spams a human every few minutes during a real incident (noise, alert fatigue); a too-long or broken cooldown means a genuinely stuck bridge report is delivered once and then silently suppressed for the rest of the incident.
**Mitigation:** Use the file-sentinel `_alert_cooldown_open()` primitive defined in Technical Approach: read the sentinel's mtime, and only stamp (`touch()`, create-or-refresh) when the window is open. This is a deliberate, decided design choice — the module already locks with files (`RECOVERY_LOCK`), so the alert cooldown stays in the same idiom rather than introducing a Redis dependency on the alert path. Redis `SET NX EX` is a *conceptually* equivalent primitive but is explicitly **not** used here (see Race Conditions for why the file sentinel is sufficient given the single-process-per-tick execution model). Test both the "still on cooldown" (second call within the window is a no-op) and "cooldown expired, re-alert" (mtime older than `WATCHDOG_ALERT_COOLDOWN_SECONDS` → fires again and re-stamps) paths explicitly.

### Risk 3: The alert notification itself depends on infrastructure that may be unhealthy during the exact incident it's meant to report
**Impact:** `send_hibernation_notification()`-style alerts work by enqueueing an AgentSession for the *worker* process to execute and send via the bridge's outbound Telegram path. If the bridge is wedged only on the *inbound* side (the actual failure mode here — Telethon stops delivering `NewMessage` events, not necessarily stops sending), outbound delivery should still work. But if recovery level 1 (process not running) is what's escalating, there's no bridge process alive to relay the message at all.
**Mitigation:** Document this limitation explicitly rather than solving it in this plan — the alert is a best-effort improvement over today's complete silence, not a guaranteed-delivery guarantee. `log_crash()` and the `logs/watchdog.log` critical entry remain the durable, always-available record regardless of whether the Telegram alert itself gets through.

## Race Conditions

No race conditions identified beyond what already exists in this file. `monitoring/bridge_watchdog.py` runs as a single, non-concurrent process per launchd tick (`StartInterval=60` spawns one process at a time; the existing `RECOVERY_LOCK` file already guards against overlapping recovery attempts if a tick runs long). Because there is exactly one recovering process at a time, the alert cooldown does **not** need a cross-process atomic compare-and-set: the `_alert_cooldown_open()` read-mtime-then-`touch()` sequence is safe under this single-writer model, exactly as `RECOVERY_LOCK`'s own non-atomic `.exists()` + `write_text()` + `.unlink()` sequence already is.

The one theoretical overlap — a manually-triggered run coinciding with the scheduled launchd tick — is neutralized without any atomic primitive: `--check-only` is read-only and never stamps the sentinel (it reads mtime via the non-stamping path, per Technical Approach), so it cannot consume or race the cooldown window. In the vanishingly rare case two *recovering* processes overlapped, the worst outcome is one duplicate Telegram alert — strictly better than today's total silence, and not worth a Redis dependency to prevent. `O_EXCL` is unusable here anyway (it cannot refresh an existing sentinel's mtime — the create-or-refresh semantics the cooldown requires; see C1 in Technical Approach), which is the concrete reason the primitive is `touch()`-based rather than exclusive-create.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1408] Migrating away from Telethon or otherwise eliminating the underlying update-loop wedge at its source — already tracked and partially mitigated by #1408; this plan only fixes the escalation ladder's response to the wedge recurring.
- Nothing else deferred — every item needed to break the level-5 livelock and restore the documented alert behavior is in scope for this plan.

## Update System

No update system changes required — this is a single-file logic fix to `monitoring/bridge_watchdog.py`, deployed the same way as any other code change (`git pull` + existing launchd service definitions already reference this same script path and interval; no new config, no new dependencies, no migration for existing installations).

## Agent Integration

No new tool/MCP surface is needed. The human-alert path reuses the existing pattern already wired end-to-end: `send_hibernation_notification()`-style AgentSession enqueue → worker executes the session → session sends a Telegram message via the bridge's existing send path. This plan adds one new call site following that same established pattern; it does not add a new CLI entry point, MCP tool, or bridge-internal import surface. Integration test: the new regression test in `tests/unit/test_bridge_watchdog.py` should mock the `AgentSession`-enqueue call (matching how `send_hibernation_notification` itself is tested, if a test for it exists — check `tests/` for `test_sustainability.py` or similar and mirror its mocking approach) rather than actually creating a session or sending Telegram traffic in unit tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md`: correct the 5-level escalation table (level 5 is no longer a distinct "do nothing but log" action; describe it as a deduplicated alert layered on top of whatever action level 1-4 actually executes, plus the level-4 "recovery exhausted" fallback that now also alerts — B1), document the reason-aware non-wedge restart circuit breaker (C2), and update the "Recovery" paragraph under "3a. Update-Loop Wedged Detector" to reflect that the level-2 cap is now actually honored end-to-end (previously true only in isolation, not in the aggregate dispatch).
- [ ] No new entry needed in `docs/features/README.md` — this is a correction to existing documented behavior, not a new feature.

### Inline Documentation
- [ ] Update or remove the now-partially-inaccurate code comment "Level cap: the wedge detector contributes at most level 2 ... must never push recovery_level to 4" in `assess_update_flow()` if the field split changes what "recovery_level" means at that call site — replace with an accurate description of the corrected two-signal (action level / alert flag) design.
- [ ] Add a docstring/comment on the new alert function explaining the cooldown rationale (mirroring the level of detail in `_inject_watchdog_steer()`'s docstring in `monitoring/session_watchdog.py`, which is the closest existing precedent in this codebase).

Both sub-items above are required — this plan corrects a doc/code drift, so both the feature doc and inline comments must change.

## Success Criteria

- [ ] A simulated wedge-caused crash streak (>= 5 crashes in 30 min, all `bridge_update_loop_wedged`) results in `execute_recovery()` being called with the real action level (2) every tick, not a no-op level.
- [ ] A simulated **non-wedge** crash storm (>= 5 crashes in 30 min, reasons other than the wedge) sets `restart_circuit_open`, suppresses the restart for that tick (no `execute_recovery()` restart call), and still fires the alert (C2 throttle preserved).
- [ ] A human-visible alert fires exactly once per cooldown window under a sustained crash storm, not once per 60-second tick — and the level-4 "recovery exhausted" fallback fires the same alert (B1).
- [ ] `docs/features/bridge-self-healing.md` accurately describes the corrected behavior.
- [ ] Level 4 (auto-revert) still requires `AUTO_REVERT_ENABLED_FILE` and is unreachable via the wedge-detector or crash-count paths alone.
- [ ] **Live post-deploy check (C4):** after merge and `/update` on the bridge machine, run `python monitoring/bridge_watchdog.py --check-only` and confirm it reports `Recovery level:` in 0-4 (never 5) plus the new `Human alert needed:` / `Alert on cooldown:` / `Restart circuit open:` lines; then `tail -n 50 logs/watchdog.log` and confirm there is no pinned "Executing recovery level 5" loop (the observed livelock signature) — real action-level restarts appear if any recovery is happening, or the bridge is simply healthy. Record the observed output in the PR/merge notes.
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
- In `monitoring/bridge_watchdog.py`, remove the `recovery_level = max(recovery_level, 5)` override (current line 513) from the recent-crash-count check in `check_bridge_health()`; replace with a `human_alert_needed` boolean on `HealthStatus`.
- In the same check, compute `restart_circuit_open` (C2): using `CrashEvent.reason`, set it true when `len(recent_crashes) >= CRASH_STORM_THRESHOLD` (default 5, promoted to a named constant) and the storm is not wedge-dominated (`wedge_count < len(recent_crashes) * WEDGE_DOMINANCE_FRACTION`, default 0.5). Add both constants with a grain-of-salt provisional/tunable comment. Add `human_alert_needed: bool = False` and `restart_circuit_open: bool = False` to the `HealthStatus` dataclass.
- **B1 (mandatory, same task):** extract the level-5 branch body into `_recovery_exhausted(issues: list[str]) -> bool` (keeps `logger.critical(...)` + `log_crash("… Recovery exhausted")`, adds `_alert_human_of_crash_storm(issues)`, returns False). Delete the `elif level == 5:` branch in `execute_recovery()`. Change **both** internal `return execute_recovery(5, issues)` call sites in the level-4 branch (current lines 720 and 735) to `return _recovery_exhausted(issues)`. The real action level (1-4, or 0/healthy) always executes as computed by the individual checks; level 5 is no longer a dispatch target.
- In `run_health_check()`: fire `_alert_human_of_crash_storm(status.issues)` when `status.human_alert_needed`; then if `status.restart_circuit_open`, log critical and `return False` without calling `execute_recovery()`; otherwise call `execute_recovery(status.recovery_level, status.issues)`.
- Update `--check-only` output in `main()` to print `Human alert needed:`, `Alert on cooldown:` (read-only mtime check, no stamping), and `Restart circuit open:` alongside the existing `Recovery level:` line.

### 2. Implement deduplicated human alert
- **Task ID**: build-watchdog-alert
- **Depends On**: build-watchdog-escalation-split
- **Validates**: tests/unit/test_bridge_watchdog.py (new alert + cooldown tests)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_alert_human_of_crash_storm(issues: list[str]) -> None` modeled on `send_hibernation_notification()` in `agent/sustainability.py`; fail-quiet, never raises. The cooldown gate lives inside this function so every caller (the `human_alert_needed` path and `_recovery_exhausted`) shares one dedup window.
- Implement the cooldown as `_alert_cooldown_open() -> bool` (C1): read `COOLDOWN_FILE.stat().st_mtime`; if within `WATCHDOG_ALERT_COOLDOWN_SECONDS` return False; otherwise `mkdir(parents=True, exist_ok=True)` + `COOLDOWN_FILE.touch()` (create-or-refresh mtime) and return True. Do NOT use `O_EXCL`. Sentinel path `data/bridge-watchdog-alert-cooldown`; 30-minute default exposed as env-overridable `WATCHDOG_ALERT_COOLDOWN_SECONDS`.
- Confirm the alert call is wired into `run_health_check()` (done in Step 1) so it fires whenever `human_alert_needed` is true and the cooldown slot is open, independent of which action level ran that tick.

### 3. Update existing tests + add regression coverage
- **Task ID**: build-watchdog-tests
- **Depends On**: build-watchdog-alert
- **Validates**: tests/unit/test_bridge_watchdog.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `HealthStatus` fixture construction sites across `TestHealthStatus`, `TestCheckOnlyOutput`, and `TestCrashDetectionOnBridgeDeath` for the two new fields (defaults keep existing fixtures valid).
- Add the livelock regression test (>= 5 wedge-reason crashes → action level still executes; alert fires once, not every tick).
- Add the non-wedge circuit-breaker test (C2) and the level-4 recovery-exhausted fallback test (B1).
- Add the cooldown-not-yet-open / cooldown-expired test pair (mtime within window is a no-op; mtime older than the window re-fires and re-stamps via `touch()`).

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
| No orphaned level-5 internal calls remain (B1) | `grep -n "execute_recovery(5" monitoring/bridge_watchdog.py` | exit code != 0 |
| Recovery-exhausted helper exists (B1) | `grep -n "_recovery_exhausted" monitoring/bridge_watchdog.py` | output > 0 |
| Reason-aware circuit breaker present (C2) | `grep -n "restart_circuit_open" monitoring/bridge_watchdog.py` | output > 0 |
| Cooldown uses touch, not O_EXCL (C1) | `grep -n "O_EXCL" monitoring/bridge_watchdog.py` | exit code != 0 |
| Docs updated | `grep -c "human_alert_needed\|deduplicated alert\|alert.*layered" docs/features/bridge-self-healing.md` | output > 0 |
| Live post-deploy check (C4) | on the bridge machine, `python monitoring/bridge_watchdog.py --check-only` then `tail -n 50 logs/watchdog.log` | `Recovery level:` is 0-4 (never 5); no pinned "Executing recovery level 5" loop |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique | B1: Orphaned `execute_recovery(5, …)` internal call sites in the level-4 branch (lines 720, 735) reuse level-5's critical logging; deleting level 5 drops them to bare `return False`, losing critical-failure logging. | Technical Approach (`_recovery_exhausted` extraction), Step 1 (mandatory same-task), Verification (B1 grep rows), Test Impact (B1 fallback test) | Level-5 body extracted into `_recovery_exhausted()`; both call sites rerouted to it; helper also fires the dedup alert. |
| Blocker | Critique | B2: Cooldown contradiction — OQ2/Technical Approach say file mtime sentinel; Risk 2/Race Conditions prescribe Redis `SET NX EX`. | Risk 2 + Race Conditions rewritten to the file-sentinel design; Redis demoted to concept-only. | Single authoritative primitive: `_alert_cooldown_open()` file sentinel; Redis explicitly not used. |
| Concern | Critique | C1: `O_EXCL` + mtime-expiry unworkable. | Technical Approach (C1), Resolved Decisions OQ2, Verification (no-O_EXCL grep). | Replaced with read-mtime-then-`touch()` create-or-refresh; `O_EXCL` explicitly rejected with reason. |
| Concern | Critique | C2: loss of the only restart throttle for non-wedge failures. | Solution key element + Technical Approach (reason-aware circuit breaker), Risk 1, Success Criteria, Test Impact. | `restart_circuit_open` suppresses restart for non-wedge storms while still alerting; wedge storms always restart. |
| Concern | Critique | C3: two fixes bundled into one Small plan. | Appetite section justification. | Kept together — any split yields an intermediate `main` worse than status quo; still small (3 helpers, 2 fields, 3 edited functions, one file). |
| Concern | Critique | C4: success criteria all unit/grep-level. | Success Criteria (live post-deploy check), Verification (live check row). | Post-merge `--check-only` + `logs/watchdog.log` tail on the bridge machine, recorded in merge notes. |

---

## Resolved Decisions

The three open questions were implementer's-choice tradeoffs with documented defaults; all resolved at finalization and folded into the Technical Approach:

1. **Field naming (OQ1):** Add `HealthStatus.human_alert_needed: bool`; leave `recovery_level` as the existing 0-4 action level. The crash-count rule only sets the alert boolean, never elevates `recovery_level`. Reads most clearly against the existing doc table and `--check-only` consumers.
2. **Cooldown storage (OQ2, C1):** File-based sentinel `data/bridge-watchdog-alert-cooldown`, mtime-based, in the same `DATA_DIR` as `RECOVERY_LOCK`. The gate is a read-mtime-then-`touch()` (create-or-refresh) primitive, `_alert_cooldown_open()`, **not** `O_EXCL` exclusive-create — `O_EXCL` fails on an existing file and so cannot refresh a persistent expiry sentinel's mtime, which is exactly what this cooldown needs. Chosen over Redis `SET NX EX` to stay consistent with the module's own file-locking convention and avoid a new Redis dependency on the alert path; the single-process-per-tick model (see Race Conditions) makes a cross-process atomic CAS unnecessary.
3. **Cooldown duration (OQ3):** 30 minutes, matching the crash-count window, exposed as `WATCHDOG_ALERT_COOLDOWN_SECONDS` (env-overridable, per the existing `WATCHDOG_*` convention) so multi-hour-incident alert fatigue is tunable without a code change.
