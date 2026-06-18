---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/valorengels/ai/issues/1724
last_comment_id:
---

# Recover hung granite sessions promptly: progress-based liveness for the priming wedge AND the mid-run wedge

## Problem

As of commit `ef53a88f`, the in-container 120s `dev_hang`/`pm_hang` deadline became a 12h *sanity ceiling* (`CYCLE_IDLE_TIMEOUT_S = 12 * 60 * 60.0`, `agent/granite_container/container.py:138`), with the code comment explicitly delegating hang detection to "the heartbeat / liveness-recovery layer (agent/session_health.py + issue #1724)." **The session-health/liveness layer is now the sole hang detector for granite sessions.** This issue therefore owns the whole problem, in two distinct wedge shapes:

**(a) Priming wedge (`never_started`).** A granite session is `running` but produced no turn (`last_turn_at = None`, `sdk_ever_output = False`), while its watchdog writes fresh `last_heartbeat_at`/`last_sdk_heartbeat_at` every ~60s. Two detectors disagree:
- `reflections/stall_advisory.py::run_stall_advisory` classifies it `STALLED reason=never_started` at a 120s grace (`agent/session_stall_classifier.py::NEVER_STARTED_GRACE_SECS = 120`), but is **advisory-only** — logs/optionally-alerts, never recovers.
- `agent/session_health.py::_has_progress` sub-check B treats the fresh heartbeat as "alive" across `300s <= running_seconds <= NO_OUTPUT_BUDGET_SECONDS (1800s)` (`session_health.py:784-824`), and even past 1800s the granite PTY pair's live children let `_tier2_reprieve_signal`'s `children`/`alive` gates grant up to `MAX_NO_OUTPUT_REPRIEVES (20)` reprieves (`:943`). Net: ~100 min before recovery, not ~300s.

**(b) Mid-run wedge.** A session that produced turns (`sdk_ever_output = True`) then genuinely hangs during a long Dev turn — the TUI froze/crashed but the `claude` process is still alive. The container used to catch this at 120s; that deadline is now 12h. Worse, the session-health layer **cannot** catch it today: `_check_tool_timeout` would flag the stuck in-flight tool, but `_tier2_reprieve_signal`'s `"alive"` gate reprieves any session whose process exists — and a hung-but-alive process passes `"alive"` forever. So a mid-run wedge is unrecoverable for up to 12h.

### Why the obvious signals don't work
- **Heartbeats lie.** The watchdog ticks on a schedule regardless of real work (the original bug). Not progress evidence.
- **Process-alive lies.** A hung TUI keeps an alive `claude` process; `"alive"` reprieve fires forever. Not progress evidence.
- **Transcript silence lies (the critical one).** The transcript tailer (`agent/granite_container/transcript_tailer.py`) folds an **append-only JSONL**: `byte_offset`, `total_*_tokens`, `turn_count`, and `last_tool_use_at` only advance when an assistant/user event lands. During a legitimate long `Task` subagent or long tool, **no event lands in the parent transcript for the entire run** (observed: subagents run 25+ min). All those fields freeze on a perfectly healthy session. A naive "transcript unchanged for N minutes → recover" rule would false-kill exactly the long-subagent case — the same mistake the old 120s `dev_hang` made.

### The reliable oracle
The signal that actually distinguishes alive from hung is the one the operator uses by eye: **the TUI screen repainting** — the spinner and token/elapsed counters tick continuously while work is happening, and freeze on a crash. `agent/granite_container/pty_driver.py` already computes byte-quiescence (C5 idle: a hung PTY goes byte-silent within ~seconds; a working PTY emits continuously), but this liveness is transient inside the container read loop and **is never persisted onto the `AgentSession`**. Capturing it is the core of the mid-run fix.

### Desired outcome
- **Priming wedge:** a `running` + `last_turn_at = None` + fresh-heartbeat session is recovered on a grace reconciled with the advisory's 120s detection grace — and the fix closes **both** sub-check B's band and the reprieve-cap path.
- **Mid-run wedge:** a session whose **PTY screen has gone quiescent** for a generous window (no spinner/counter repaint) while the transcript has not advanced is recovered well below the 12h ceiling — **without** killing a legitimately long, actively-repainting subagent/tool turn.
- Grace/window values derive from single shared sources of truth so detection and action cannot drift.

### Real observation (2026-06-18)
Session `tg_valor_-1003449100931_993` (`agent_session_id 7f819c9a7efa46f1b2b8b10ef1d34dfc`) was `running` + heartbeating with `last_turn_at = None` for ~5 min (priming wedge). `stall-advisory` logged `STALLED reason=never_started elapsed_secs=247 grace_secs=120` at 02:48:53; nothing recovered it until a manual `valor-service.sh restart`.

## Freshness Check

**Baseline commit at original plan:** b414eed1 (recon for the narrow scope). **Re-baselined to:** ef53a88f + the transcript-tailer fixes (995bc453, 91289fc3, b414eed1).
**Disposition:** **CHANGED.** The original plan assumed (1) scope = priming wedge only, and (2) granite exposes no usable progress fields. Both are now false: `ef53a88f` removed the container's 120s hang detector (mid-run wedge now in scope), and the tailer fixes populate real progress fields. This plan supersedes the narrow version.

**File:line references re-verified against current main:**
- `agent/granite_container/container.py:121-138` — `CYCLE_IDLE_TIMEOUT_S` now 12h, comment delegates to session_health + #1724. ✓
- `reflections/stall_advisory.py:38-161` — advisory-only, zero writes. ✓
- `agent/session_stall_classifier.py:50` — `NEVER_STARTED_GRACE_SECS = 120`. ✓
- `agent/session_health.py:207-269` — `AGENT_SESSION_HEALTH_MIN_RUNNING` / `STARTUP_GRACE_SECONDS = 300` (race-guard floor, env-tunable). ✓
- `agent/session_health.py:284` — `NO_OUTPUT_BUDGET_SECONDS = MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW = 1800`. ✓
- `agent/session_health.py:345-369` — `_check_tool_timeout`, tiers 30/120/300s. ✓
- `agent/session_health.py:784-824` — sub-check B fresh-heartbeat band. ✓
- `agent/session_health.py:884-981` — `_tier2_reprieve_signal` (`compacting`/`children`/`alive`; cap at `:943`). ✓
- `agent/granite_container/bridge_adapter.py:626-648` — `_bump_last_turn_at` (flips `sdk_ever_output`). ✓
- `agent/granite_container/bridge_adapter.py:837-934` — diff-gated tailer save of `turn_count`/`current_tool_name`/`last_tool_use_at`/`byte_offset`/token counts. ✓
- `agent/granite_container/transcript_tailer.py:105-174` — append-only fold; confirms transcript freezes during in-flight tool with no following event. ✓
- `agent/granite_container/pty_driver.py:36, 212, 353, 491` — byte-quiescence (C5 idle) computed but not persisted. ✓

**Cited sibling issues/PRs re-checked:** #1356 (CLOSED, introduced 1800s gate), #1614 (CLOSED, gated sticky own-progress on heartbeat freshness), #1172 (CLOSED, "kill only on positive no-progress evidence, never on staleness"), #1226 (CLOSED, the 20-tick reprieve cap). All resolutions unchanged.

**Test-path correction (critique B2):** the original plan cited `tests/unit/test_agent_session_health_monitor.py` 4×. That file lives in **`tests/integration/`**. Corrected throughout this plan.

## Prior Art

- **#1356** (closed): bounded sub-check B's previously-infinite fast-path at `NO_OUTPUT_BUDGET_SECONDS = 1800`. **Partial** — 30 min is still long, the value is internally-derived (`20 * 90`) with no tie to the advisory's 120s, and it left the reprieve-cap path untouched. Direct parent of this issue. **Do not repeat its partial-fix shape** (touching only one of the two gates).
- **#1614** (closed): gated sticky own-progress fields (`turn_count`/`log_path`/`claude_session_uuid`) on heartbeat freshness so a stale `running` session can't evade recovery. **Success.** Reuse `tests/unit/test_session_health_inference_removed.py` as the regression-test model.
- **#1172** (closed): retired wall-clock caps and the "stdout" reprieve gate; established "kill only on positive no-progress evidence, never infer death from staleness." **Constrains this fix**: the mid-run oracle must be positive evidence of a frozen screen, not mere transcript staleness.
- **#1226** (closed): added `MAX_NO_OUTPUT_REPRIEVES` cap (only for `sdk_ever_output=False`). The priming wedge survives it because the granite pair keeps live children → `children`/`alive` reprieves keep firing until the cap.
- **`ef53a88f`** (this week): demoted the container's 120s deadline to a 12h ceiling and handed hang detection to this layer. Read its `container.py:121-138` comment — it states the intent this plan fulfills.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1356 | Bounded sub-check B at 1800s | Value untied to the advisory's 120s; ignored the reprieve-cap path; 30 min still long for a zero-progress session. |
| #1226 | 20-tick reprieve cap (no-output only) | Same 1800s-class timescale; the priming pair's live children keep `children`/`alive` reprieves firing up to the cap. |
| container 120s `dev_hang` | Killed any PTY not idle within 120s | Killed legit multi-minute turns (false positive) → removed in `ef53a88f`, leaving a 12h hole for the mid-run wedge. |

**Root-cause pattern:** every prior signal (heartbeat, process-alive, transcript-staleness, wall-clock) is either a false-positive (kills long-but-working turns) or a false-negative (reprieves hung-but-alive sessions forever). None measures the one thing that is ground truth: **is the TUI screen still doing anything.**

## Data Flow

**Priming wedge (path A):** bridge enqueues Eng `AgentSession` → worker `status=running`, spawns granite PTY pair → `_heartbeat_loop` ticks → priming runs, no `turn_start`, `last_turn_at=None`, `sdk_ever_output=False`. Advisory classifies `never_started` at 120s (read-only, dead end). Recovery actor `_agent_session_health_check` (`:1532`) only evaluates `_has_progress` when `running_seconds > 300s`; sub-check B returns True up to 1800s; past that, `_tier2_reprieve_signal` `children`/`alive` reprieves the live pair up to the 20-cap (~100 min) → finally `_apply_recovery_transition(reason_kind="no_progress")` → `running -> pending`.

**Mid-run wedge (path B):** session produces turns (`sdk_ever_output=True`) → enters a long Dev turn / `Task` subagent → parent transcript stops emitting events (byte_offset/tokens/turn_count/last_tool_use_at all freeze) → if the turn genuinely hangs, the TUI screen freezes but the `claude` process stays alive. `_check_tool_timeout` flags the stuck `current_tool_name`, but `_tier2_reprieve_signal` `"alive"` reprieves it forever (process exists). No recovery until the 12h ceiling.

The fix lands in: (path A) sub-check B's never-started leg **and** the reprieve evaluation; (path B) a new PTY-activity signal persisted from the container read loop, consumed by a new mid-run leg in the recovery actor.

## Architectural Impact

- **New persisted signal:** add `last_pty_activity_at` (datetime) to `AgentSession`, written by the granite container/`bridge_adapter` whenever the PTY read loop observes non-quiescent screen bytes (the same byte-activity `pty_driver` already detects for C5 idle). This is the only new field; per `feedback_field_backcompat_heal`, nullable AgentSession fields need no extra back-compat code (`_heal_descriptor_pollution` walks fields generically). Non-granite sessions leave it `None` and are unaffected.
- **Import direction (unchanged constraint):** `session_health` → `session_stall_classifier` only (shared grace constant); the classifier must never import `session_health` (guarded by `tests/integration/test_stall_advisory_e2e.py`). The new PTY-activity field is plain data on the model — no new module coupling.
- **Single writer for recovery:** only `session_health` ever writes recovery transitions; `stall_advisory` stays read-only. No double-action.
- **Reversibility:** Medium-high. Path-A change is a tighter branch + reprieve guard + one constant. Path-B adds a field + a writer + a recovery leg; revertible by ignoring the field and restoring the prior reprieve behavior.

## Appetite

**Size:** Large (was Medium — mid-run scope + the new persisted PTY signal raise it).

**Team:** debugging-specialist (recovery logic + reprieve guard), an agent for the container/tailer PTY-activity writer, test-engineer (both wedge regressions), validator, documentarian.

**Interactions:** PM check-ins 1-2 (the PTY-activity-signal approach and the mid-run window are the load-bearing decisions); review rounds 1-2 (this is now the sole hang detector — careful pass on both false-kill and false-negative).

## Prerequisites

None external. The transcript-tailer progress fields and PTY byte-quiescence detection already exist on main; this work consumes and persists them.

## Solution

### Decisions locked (from PM, 2026-06-18)
- **D1 (priming recovery timescale): lower the floor for `never_started`.** The 300s race guard cannot honor the advisory's 120s. For `never_started` sessions specifically, allow the recovery actor to evaluate/act before 300s, reconciled to the shared `NEVER_STARTED_GRACE_SECS (120s)` plus a small confirmation margin — rather than accepting ~300s. This is a deliberate, scoped relaxation of the race guard for the no-turn case only (general sessions keep the 300s floor).
- **D2 (mid-run oracle): PTY-screen activity, not transcript silence.** Recover a mid-run session only when `last_pty_activity_at` has been quiescent beyond the mid-run window AND the transcript has not advanced — never on transcript-staleness alone, because a long subagent legitimately freezes the transcript while the screen keeps repainting.
- **D3 (reprieve cap, educated guess to validate in build): bypass the `children`/`alive` reprieve for `never_started`.** Live PTY children are *expected* during priming and are not progress evidence, so a no-turn session past its grace must not be reprieved by `children`/`alive`. Validate via tests that this does not regress a genuinely-still-priming session that is about to emit its first turn.

### Key Elements
1. **Shared never-started grace** — `session_health` reads `NEVER_STARTED_GRACE_SECS` (already in `session_stall_classifier.py`) plus a small `NEVER_STARTED_CONFIRM_MARGIN_SECS` so detection (advisory) and action (actor) derive from one source. No second magic number.
2. **Never-started recovery leg (path A)** — when `not sdk_ever_output` and no turn ever: (a) sub-check B does NOT grant the heartbeat fast-path past the never-started grace, and (b) `_tier2_reprieve_signal` does NOT grant `children`/`alive` for this session (D3). The race-guard floor is relaxed to the never-started grace for this case only (D1).
3. **PTY-activity signal (path B, new)** — persist `last_pty_activity_at` from the container read loop; add a mid-run recovery leg: if `sdk_ever_output=True`, `current_tool_name` is non-null (or a turn is in flight), the transcript byte_offset has not advanced, AND `last_pty_activity_at` is older than `MID_RUN_QUIESCENCE_SECS`, recover. The `"alive"` reprieve must NOT override a confirmed frozen screen.
4. **Preserved long-subagent safety** — a session whose screen is still repainting (`last_pty_activity_at` fresh) is never recovered, regardless of how long the transcript or `last_tool_use_at` has been frozen. This is the regression guard for 25+ min subagents.

### Mid-run window (D2) — value rationale
A live TUI repaints its spinner/counters on a sub-second-to-seconds cadence; a crash freezes it immediately. So `MID_RUN_QUIESCENCE_SECS` is a *confirmation* window over an already-strong signal, not a tolerance for legit silence. Proposed default **180s** of continuous screen quiescence (env-tunable `MID_RUN_QUIESCENCE_SECS`), comfortably above transient repaint gaps yet far below the 12h ceiling. The 25+ min subagent is protected by the *nature* of the signal (its screen keeps repainting), not by the window size. **Open for confirmation** — see Open Questions Q-A.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Redis counter increments on the new fall-through legs are best-effort (log + continue); a counter failure must not block recovery (patch `incr` to raise, assert recovery still fires).
- [ ] PTY-activity writer in the container must never raise into the read loop; a write failure leaves `last_pty_activity_at` stale and falls back to the existing (process-alive) behavior — assert the writer swallows + logs.
- [ ] `classify_session_stall` keeps its swallow-all → `healthy/unclassifiable` contract; unchanged.

### Empty/Invalid Input Handling
- [ ] `last_pty_activity_at is None` (non-granite session, or granite before first paint) → the mid-run leg is skipped entirely; behavior reverts to current. Test pins this.
- [ ] `started_at=None AND created_at=None` (legacy/phantom) preserves the fast-path. Keep the existing test.
- [ ] Negative `running_seconds` (clock skew) preserves the fast-path. Keep the existing guard + test.

### Error State Rendering
- [ ] No user-visible output; observable via the `[session-health] Recovering session ...` log and project-scoped Redis counters (new: `tier1_falloff:never_started_grace_exceeded`, `mid_run_pty_quiescent_recovery`). Asserted in tests.

## Test Impact
- [ ] `tests/integration/test_agent_session_health_monitor.py` — UPDATE (note: integration, not unit): add never-started recovery cases (recover at the never-started grace, not 1800s) and the mid-run PTY-quiescence recovery case.
- [ ] `tests/unit/test_session_health_inference_removed.py` — UPDATE: add never-started-grace assertions following its sub-check-B pattern; re-verify no assumption of the old 1800s boundary for the never-started case.
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: extend to assert (a) never-started + fresh heartbeat recovers on the new grace, (b) a session that produced a turn is not recovered by the never-started leg.
- [ ] `tests/unit/test_session_stall_classifier.py` — UPDATE: pin `NEVER_STARTED_GRACE_SECS` and the new `NEVER_STARTED_CONFIRM_MARGIN_SECS` relationship.
- [ ] `tests/unit/test_session_health_compacting_reprieve.py` — UPDATE/VERIFY: the D3 reprieve-bypass for never-started must not break the existing `compacting`/`children`/`alive` reprieve behavior for output-producing sessions.
- [ ] `tests/integration/test_stall_advisory_e2e.py` — VERIFY (no change expected): the `sys.modules` import-direction guard still holds.
- [ ] NEW `tests/integration/` case — mid-run wedge: `last_pty_activity_at` stale > window + transcript frozen + process alive → recovered; and the inverse: `last_pty_activity_at` fresh (screen repainting) + transcript frozen 25+ min → NOT recovered.

No tests DELETEd.

## Rabbit Holes
- **Lowering the global 300s race guard.** D1 relaxes it for `never_started` ONLY. Do not touch the floor for sessions that have produced output — it protects genuinely-fresh sessions loop-wide.
- **Using transcript staleness as the mid-run trigger.** Explicitly rejected (false-kills long subagents). The mid-run trigger is PTY quiescence; transcript-not-advanced is a corroborating condition, never the sole one.
- **Parsing the TUI screen for token/elapsed counters.** Tempting to read the exact counter values; unnecessary and brittle. Byte-activity (screen changed at all) is sufficient and is what `pty_driver` already computes.
- **Giving `stall_advisory` kill powers.** Breaks the classifier's zero-writes / no-session_health-import contract; reintroduces competing recoverers. Rejected.
- **Storing `sdk_ever_output`.** It stays derived from `last_tool_use_at`/`last_turn_at`.
- **Discovering subagent transcript files as the liveness signal.** A plausible alternative to PTY-activity (a Task subagent writes its own growing JSONL), but it requires mapping parent→child transcripts and handling nested/Bash tools that have no transcript. PTY-activity is uniform across all tool types. Note as the fallback if PTY-activity proves unreliable in build; do not build both.

## Risks

### Risk 1: False-kill of a long, actively-working subagent/turn (the #1 regression)
**Impact:** A 25+ min subagent is recovered mid-flight — wasted work, looks flaky, repeats the old `dev_hang` mistake.
**Mitigation:** The mid-run trigger requires `last_pty_activity_at` quiescent for the full window. A working turn repaints continuously, so its activity timestamp stays fresh. Regression test pins "fresh PTY activity + frozen transcript 25 min → NOT recovered."

### Risk 2: PTY-activity signal not actually written / too coarse
**Impact:** If the container doesn't persist screen activity reliably, the mid-run leg either never fires (false-negative restored) or fires wrongly.
**Mitigation:** Source the timestamp from the same byte-read path `pty_driver` already uses for C5 idle; integration test drives a real PTY pair and asserts the field advances during work and freezes on a killed child. If unreliable, fall back to the subagent-transcript approach (Rabbit Holes).

### Risk 3: D3 reprieve-bypass kills a session about to emit its first turn
**Impact:** A slow-but-genuinely-priming session is recovered just before its first turn lands.
**Mitigation:** The never-started grace + confirmation margin gives priming headroom; the moment a turn lands, `sdk_ever_output` flips True and the never-started legs no longer apply. Regression test: a session that emits a turn within the grace+margin is not recovered.

### Risk 4: Constant/grace drift reintroduced later
**Mitigation:** Single shared `NEVER_STARTED_GRACE_SECS`; unit test pins the detection/action relationship so a divergent edit fails CI.

## Race Conditions

### Race 1: Recovery vs. first turn arriving (path A)
**Mitigation:** `_has_progress` checks `sdk_ever_output` first; `_apply_recovery_transition` uses CAS (`expected_status="running"`). Worst case: one benign re-queue; the resumed session continues from its transcript.

### Race 2: Recovery vs. subagent finishing (path B)
**Trigger:** the subagent returns (screen repaints, transcript advances) in the same tick the actor reads a stale `last_pty_activity_at`.
**Mitigation:** require BOTH PTY quiescence beyond the window AND no byte_offset advance since the prior tick; CAS on the transition. A turn landing flips the conditions; worst case a single benign re-queue.

### Race 3: Advisory and actor on the same session
**Mitigation:** advisory is read-only; only `session_health` writes. No double-action.

## No-Gos (Out of Scope)
Not changing: the global 300s race guard for output-producing sessions, the 1800s output-then-idle path for the non-granite case, the advisory's 120s detection cadence, the derived nature of `sdk_ever_output`. Not building the subagent-transcript discovery path unless PTY-activity proves unreliable.

## Update System
No update-system changes required — purely internal worker/container logic. New env knobs (`NEVER_STARTED_CONFIRM_MARGIN_SECS`, `MID_RUN_QUIESCENCE_SECS`) have safe defaults and need no propagation; document them in `.env.example` only if we want operator-tunability. The change ships with the next `/update` pull-and-restart (`./scripts/valor-service.sh restart` to cycle worker + the granite container code).

## Agent Integration
No agent integration required — worker/container-internal recovery logic. No new CLI entry point, MCP tool, or bridge import. Observable via the `[session-health] Recovering session ...` log and project-scoped Redis counters (`tier1_falloff:never_started_grace_exceeded`, `mid_run_pty_quiescent_recovery`) and the unchanged stall-advisory alert.

## Documentation
### Feature Documentation
- [ ] Update the canonical recovery doc (locate during build: `grep -rl "NO_OUTPUT_BUDGET\|_has_progress\|_tier2_reprieve" docs/`; likely `docs/features/session-lifecycle.md` and/or `docs/features/granite-pty-production.md`) to describe both recovery legs and the `last_pty_activity_at` signal.
- [ ] Document that the container's 12h `CYCLE_IDLE_TIMEOUT_S` is a ceiling and the session-health layer is the real hang detector (cross-link `ef53a88f`).

### Inline Documentation
- [ ] Update `_has_progress` and `_tier2_reprieve_signal` docstrings for the never-started legs.
- [ ] Comment the new shared grace usage and the `last_pty_activity_at` writer site.

## Success Criteria
- [ ] Priming wedge: `running` + `last_turn_at=None` + fresh heartbeats is recovered on the never-started grace (reconciled to 120s + margin via D1), NOT after ~30-100 min — closing both sub-check B and the reprieve path.
- [ ] Mid-run wedge: a session with frozen PTY screen (`last_pty_activity_at` quiescent > window) + non-advancing transcript + alive process is recovered well below the 12h ceiling.
- [ ] No false-kill: a long subagent whose screen keeps repainting (fresh `last_pty_activity_at`) is NOT recovered even with a 25+ min frozen transcript — regression test green.
- [ ] No false-kill: a session that emits its first turn within the never-started grace+margin is NOT recovered.
- [ ] Detection and action graces derive from a single shared constant; a drift test fails CI.
- [ ] `last_pty_activity_at` advances during real PTY work and freezes on a killed child — integration test green.
- [ ] Advisory stays read-only; only `session_health` writes (import-guard test green).
- [ ] New Redis counters distinguish the two recovery legs on dashboards.
- [ ] Tests pass (`/do-test`); docs updated (`/do-docs`); ruff clean.

## Team Orchestration
The lead agent orchestrates via Task tools and NEVER builds directly.

### Team Members
- **recovery-builder** (debugging-specialist) — path-A never-started leg, reprieve bypass (D3), shared grace, counters. Resume: true.
- **pty-signal-builder** (agent-architect) — persist `last_pty_activity_at` from the container/`bridge_adapter` read loop; path-B mid-run recovery leg. Resume: true.
- **regression-tester** (test-engineer) — both wedge recover/do-not-recover suites + drift pin + PTY-activity integration test. Resume: true.
- **recovery-validator** (validator) — verify long-subagent not killed, slow-priming not killed, import direction, advisory read-only. Resume: true.
- **docs-writer** (documentarian) — recovery feature doc + docstrings.

## Step by Step Tasks

### 1. Shared never-started grace + confirmation margin
- **Task ID**: build-shared-grace
- **Depends On**: none
- **Validates**: tests/unit/test_session_stall_classifier.py
- **Assigned To**: recovery-builder · debugging-specialist · Parallel: false
- Add `NEVER_STARTED_CONFIRM_MARGIN_SECS` next to `NEVER_STARTED_GRACE_SECS` in `session_stall_classifier.py`; `session_health` imports both. Document the detection-vs-action relationship.

### 2. Path-A never-started recovery (sub-check B leg + reprieve bypass + floor relaxation)
- **Task ID**: build-never-started
- **Depends On**: build-shared-grace
- **Validates**: tests/integration/test_agent_session_health_monitor.py, tests/unit/test_session_health_inference_removed.py, tests/unit/test_session_health_compacting_reprieve.py
- **Informed By**: D1 (relax 300s floor for no-turn case only), D3 (bypass children/alive reprieve for never-started)
- **Assigned To**: recovery-builder · debugging-specialist · Parallel: false
- In sub-check B: when `not sdk_ever_output` and no turn ever, deny the heartbeat fast-path past the never-started grace. In `_tier2_reprieve_signal`: when never-started past grace, skip `children`/`alive`. Relax the actor's 300s evaluation floor for the never-started case. Add `tier1_falloff:never_started_grace_exceeded` counter. Update docstrings.

### 3. Path-B PTY-activity signal + mid-run recovery leg
- **Task ID**: build-mid-run
- **Depends On**: build-shared-grace
- **Validates**: tests/integration/test_agent_session_health_monitor.py
- **Informed By**: D2 (PTY-activity oracle), transcript-freeze finding, pty_driver C5 quiescence
- **Assigned To**: pty-signal-builder · agent-architect · Parallel: true (independent of task 2; different code paths)
- Add `last_pty_activity_at` to `AgentSession`; write it from the container read loop on non-quiescent screen bytes (swallow+log on failure). Add the mid-run leg: `sdk_ever_output=True` + tool/turn in flight + byte_offset not advanced + `last_pty_activity_at` quiescent > `MID_RUN_QUIESCENCE_SECS` → recover; `"alive"` reprieve must not override. Add `mid_run_pty_quiescent_recovery` counter.

### 4. Regression + integration tests
- **Task ID**: build-tests
- **Depends On**: build-never-started, build-mid-run
- **Validates**: all UPDATEd test files + the new mid-run integration case
- **Assigned To**: regression-tester · test-engineer · Parallel: false
- Never-started recover at grace (not 1800s); turn-producing + slow-priming-about-to-turn NOT recovered. Mid-run: stale PTY + frozen transcript + alive → recover; fresh PTY + 25min frozen transcript → NOT recover. `last_pty_activity_at` advances on work / freezes on killed child. Legacy + clock-skew preserve fast-path. Counter-failure does not block recovery. Drift pin.

### 5. Validate safety + import direction
- **Task ID**: validate-recovery
- **Depends On**: build-tests
- **Assigned To**: recovery-validator · validator · Parallel: false
- Confirm both false-kill cases safe; `test_stall_advisory_e2e.py` import guard green; import direction via grep; run targeted tests.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-recovery
- **Assigned To**: docs-writer · documentarian · Parallel: false
- Update recovery feature doc(s) + docstrings + constant/field comments + docs index entry.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recovery-validator · validator · Parallel: false
- Full targeted test set + ruff; verify every Success Criterion incl. docs and import-direction grep. Final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Stall classifier tests | `pytest tests/unit/test_session_stall_classifier.py -q` | exit 0 |
| Health monitor (integration) | `pytest tests/integration/test_agent_session_health_monitor.py -q` | exit 0 |
| Inference-removed unit | `pytest tests/unit/test_session_health_inference_removed.py -q` | exit 0 |
| Reprieve unit | `pytest tests/unit/test_session_health_compacting_reprieve.py -q` | exit 0 |
| Heartbeat-progress integration | `pytest tests/integration/test_session_heartbeat_progress.py -q` | exit 0 |
| Advisory import guard | `pytest tests/integration/test_stall_advisory_e2e.py -q` | exit 0 |
| Import direction (no reverse import) | `grep -n "import session_health\|from agent.session_health" agent/session_stall_classifier.py` | exit 1 (no match) |
| Shared grace imported | `grep -n "NEVER_STARTED_GRACE_SECS\|NEVER_STARTED_CONFIRM_MARGIN_SECS" agent/session_health.py` | match |
| PTY-activity field present | `grep -n "last_pty_activity_at" models/agent_session.py agent/granite_container/bridge_adapter.py` | match in both |
| Lint clean | `python -m ruff check agent/session_health.py agent/session_stall_classifier.py agent/granite_container/bridge_adapter.py` | exit 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/session_stall_classifier.py agent/granite_container/bridge_adapter.py` | exit 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **(Q-A — mid-run window value)** `MID_RUN_QUIESCENCE_SECS` default. Proposed **180s** of continuous PTY-screen quiescence (the signal is strong; this is a confirmation margin, not silence tolerance). Confirm 180s, or prefer a different value? Env-tunable as planned?
2. **(Q-B — PTY-activity vs subagent-transcript)** The plan makes PTY-screen byte-activity the mid-run oracle (uniform across all tool types). The alternative — discovering each subagent's own growing transcript — is noted as a build-time fallback. Confirm PTY-activity is the primary approach to build first.
3. **(Q-C — D1 floor relaxation blast radius)** Relaxing the 300s race guard for the `never_started` case only: acceptable, or do you want the actor to still wait to ~300s and instead just close the reprieve path (slower recovery, zero change to the race guard)? D1 currently says relax it.
