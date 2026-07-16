---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2071
last_comment_id:
---

# Orphan hang-probe follow-ups (#2069 tail)

## Problem

PR #2070 (issue #2069) widened the never-started D0 grace from 150s to 1230s (~20 min) and added an evidence-based subprocess-hang probe so a genuinely-slow Opus cold start is no longer false-killed. Three hardening/verification follow-ups were deferred; this plan closes them.

**Current behavior:**
1. **Alive-hung-orphan recovery is slow.** When a worker dies mid-cold-start, its `claude -p` subprocess is orphaned (PPID==1) and may be alive-but-hung. A fresh worker's health loop evaluates the orphaned session via `_tier2_reprieve_signal`, but that path reads the hang-probe pid from the in-process registry handle (`handle.pid`) — which is `None` for a session the new worker never spawned. The probe returns `unknown`, so the orphan is not fast-recovered and waits the full 1230s D0 grace (an ~18-min latency increase vs. the pre-#2069 150s). The issue assumed the dead-worker sweep + PPID==1 reaper would cover this faster; recon proved they do not (sweep skips alive pids; reaper is hourly and only kills the OS process).
2. **Post-init hangs wait the full 1800s deadline** — by design, to avoid false-killing a session legitimately blocked on a non-443 endpoint. This is deliberate and worth documenting, not changing.
3. **The Fix#3 owned-task hang wiring has no direct test.** The inline block at `agent/agent_session_queue.py:2197-2205` was verified by review only.

**Desired outcome:**
1. A genuine alive-hung-orphan is recovered in ~90s (probe cadence) instead of 1230s, using the session row's persisted `claude_pid` as a fallback when no local handle exists — still evidence-only (#1172), still owner-gated (#2098).
2. The post-init-hang design tradeoff is documented with its revisit rationale.
3. A pure, unit-tested `_owned_task_hang_check` helper replaces the inline Fix#3 hang block.

## Freshness Check

**Baseline commit:** `bc1a311b4` (HEAD at plan time)
**Issue filed at:** 2026-07-13T16:27:25Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:1447` — `_tier2_reprieve_signal` reads `pid = handle.pid if handle is not None else None` — still holds.
- `agent/session_health.py:849` `_sweep_dead_worker_sessions` — skips alive pids at ~L896 (`os.kill(pid,0)` succeeds → continue) — still holds.
- `agent/session_health.py:4914` `_reap_orphan_session_processes` — hourly reflection, OS-process kill only — still holds.
- `agent/agent_session_queue.py:2197-2205` — inline Fix#3 hang block (`derive_sdk_ever_output` gate + `subprocess_hang_verdict(_hang_pid, ..., caller="fix3")`) — still holds.
- `agent/session_runner/liveness.py:201-205` — `_API_REMOTE_PORTS` env-tunable via `HANG_PROBE_API_PORTS` — still holds.

**Cited sibling issues/PRs re-checked:**
- #2069 / PR #2070 — merged; the code this plan hardens.
- #2098 (`88575cc33`, merged 2026-07-15) — gates `_agent_session_health_check` actuation to the owning worker process. Does NOT change any root cause; reinforces that a fresh worker's health loop (not the reflection process) owns orphan recovery — exactly where the sub-item 1 fix lives.

**Commits on main since issue was filed (touching referenced files):**
- `88575cc33` (#2098) — reinforces single-owner actuation (see above); no root-cause change.
- `40b239374`, `d105b33e5`, `4a5a72ff7`, `3bf7e229f` — corrupted-record / stale-index queue hardening; irrelevant to the hang-probe wiring.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** All line references current at baseline. The sub-item 1 fix must respect the #2098 single-owner gate — it does, since it runs inside `_tier2_reprieve_signal` called from the owner-gated `_agent_session_health_check`.

## Prior Art

- **PR #2070 (#2069)**: Widened never-started grace + added `subprocess_hang_verdict`. This plan is its explicit deferred tail.
- **#1172**: Evidence-only kill model — kill on positive evidence, never on output silence. The claude_pid fallback preserves this (an unreadable/gone pid → `unknown`/`hung` verdict handled identically to the handle.pid path).
- **#1271**: PPID==1 orphan reaper — the hourly OS-process cleanup this plan proves is not a timely session-recovery path.
- **#2098**: Single-owner health-check actuation — the gate the fix runs beneath.

## Data Flow

1. **Entry point**: worker dies mid-cold-start; its `claude -p` child is reparented to PID 1, alive but hung (flat CPU, no children, no API socket). Session row stays `running` with `claude_pid` set, `sdk_ever_output=False`.
2. **Fresh worker health loop** (`_agent_session_health_loop` → `_agent_session_health_check`, owner-gated per #2098) evaluates the session.
3. **`_should_kill_no_progress` → `_tier2_reprieve_signal`**: computes `pid`. **Today**: `handle = _active_sessions.get(id)` is `None` (new worker never spawned it) → `pid = None` → `subprocess_hang_verdict(None, ...)` → `("unknown", None)` → falls to reprieve-count guard / D0 grace (1230s).
4. **After fix**: when `handle.pid is None`, fall back to `getattr(entry, "claude_pid", None)`. `subprocess_hang_verdict(claude_pid, ...)` probes the orphaned tree → `hung` on the third flat poll (~90s) → `_tier2_reprieve_signal` returns `None` → recovery.
5. **Output**: orphan recovered in ~90s instead of 1230s.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `_tier2_reprieve_signal` internal pid resolution gains a fallback; no signature change. New pure helper `_owned_task_hang_check(entry, pid) -> tuple[bool, str | None]` in `agent/agent_session_queue.py`.
- **Coupling**: unchanged — the fallback reads a field already on the entry; the helper extraction reduces coupling by making the Fix#3 decision unit-testable.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — both changes are localized and revert cleanly.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **claude_pid probe fallback**: in `_tier2_reprieve_signal`, when the in-process handle yields no pid, fall back to the session row's persisted `claude_pid` so the evidence-based hang probe can run against an orphaned-but-alive subprocess.
- **`_owned_task_hang_check` helper**: a pure function extracted from the inline Fix#3 block, returning `(hang_detected, hang_gate)`, unit-tested in isolation.
- **Post-init-hang design doc**: a documented tradeoff + revisit rationale in the health-monitor feature doc.

### Flow

Worker dies mid-start → orphaned hung `claude -p` (PPID==1) → fresh worker health tick → `_tier2_reprieve_signal` probes via `claude_pid` fallback → `hung` at ~90s → recovery (was: wait 1230s).

### Technical Approach

- **Sub-item 1** (`agent/session_health.py::_tier2_reprieve_signal`): change
  `pid = handle.pid if handle is not None else None`
  to prefer `handle.pid`, else fall back to `getattr(entry, "claude_pid", None)` (coerced to `int`, guarded). The probe already treats a gone/dead/access-denied pid safely (`unknown`/`hung`), so a recycled or stale pid cannot force a false positive beyond the existing handle.pid risk surface. The fallback only helps the orphan case (handle present → unchanged behavior).
- **Sub-item 3** (`agent/agent_session_queue.py`): extract
  ```python
  def _owned_task_hang_check(entry, pid, *, caller="fix3") -> tuple[bool, str | None]:
      if derive_sdk_ever_output(entry):
          return (False, None)
      verdict, gate = subprocess_hang_verdict(pid, <session_key>, caller=caller)
      return (verdict == "hung", gate)
  ```
  and call it from the inline block (which keeps the `_active_sessions` pid lookup). Behavior identical; now unit-testable.
- **Sub-item 2**: documentation only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_tier2_reprieve_signal` and `subprocess_hang_verdict` are already never-raise (broad `except`). The claude_pid fallback adds only a guarded `int()` coercion; test that a malformed/None `claude_pid` degrades to `pid=None` → `unknown` (no raise, no false recovery).
- [ ] `_owned_task_hang_check` inherits `subprocess_hang_verdict`'s never-raise contract; test the `sdk_ever_output=True` short-circuit returns `(False, None)` without probing.

### Empty/Invalid Input Handling
- [ ] `_owned_task_hang_check(entry, None)` (no pid) → probe returns `unknown` → `(False, None)`.
- [ ] `_tier2_reprieve_signal` with `handle=None` and `entry.claude_pid=None` → `pid=None` → existing behavior (no regression).

### Error State Rendering
- [ ] Not user-visible; recovery is logged via existing `_recovery_reason` / telemetry paths. No new user-facing surface.

## Test Impact

- [ ] `tests/unit/session_runner/test_hang_probe.py` — UPDATE (add): the `TestTier2HangProbeWiring` class or a sibling gains a case asserting the claude_pid fallback fires when `handle=None`. May instead live in the health-monitor test module — builder picks the closest existing home.
- [ ] `tests/unit/` (Fix#3 loop) — ADD: new `test_owned_task_hang_check.py` (or an added class in an existing queue/session-runner test module) covering the extracted helper: confirmed-hang, hang bypasses `_should_kill_no_progress`, probe skipped when `sdk_ever_output=True`, no-pid → no hang.
- [ ] No existing test asserts the OLD `handle.pid`-only pid resolution as a hard contract, so no test breaks from the fallback (additive change). Existing `test_hang_probe.py` cases keyed on explicit pids are unaffected.

## Rabbit Holes

- **PID-recycling paranoia**: do NOT add cmdline-verification to `subprocess_hang_verdict` for the fallback. The existing probe's gone/dead/access-denied handling plus the flat-CPU-no-children-no-API confirmation is the accepted safety surface; the handle.pid path already carries the identical residual risk. Adding cmdline checks is a separate, larger change.
- **In-flight-socket-aware post-init probe** (sub-item 2): tempting but out of scope — it risks false-killing legitimate non-443 blocks. Document and defer.
- **Refactoring the whole Fix#3 loop**: extract ONLY the hang-check decision into a pure helper. Do not restructure the surrounding cancel/finalize logic.

## Risks

### Risk 1: claude_pid fallback probes a recycled PID
**Impact:** A different process that reused the orphan's pid could be misclassified. **Mitigation:** the probe only declares `hung` after `HANG_CONFIRM_SAMPLES` flat-CPU polls with no children and no established HTTPS socket; a busy replacement process trips `progressing`. The residual risk equals the already-shipped `handle.pid` path. The fallback fires only when `sdk_ever_output=False` (never-started), narrowing exposure.

### Risk 2: Helper extraction changes Fix#3 behavior
**Impact:** A subtle behavior change in the owned-task loop kill decision. **Mitigation:** the helper is a byte-for-byte behavioral extraction (same `derive_sdk_ever_output` gate, same `subprocess_hang_verdict` call, same `verdict == "hung"` test); a unit test pins the three branches, and the inline caller keeps its exact `_active_sessions` pid lookup.

## Race Conditions

### Race 1: claude_pid read vs. concurrent worker respawn
**Location:** `agent/session_health.py::_tier2_reprieve_signal`
**Trigger:** the owning worker respawns the subprocess (new claude_pid) between the health-check row read and the probe.
**Data prerequisite:** `entry` is a fresh row read within the health tick; `claude_pid` reflects the last spawn.
**State prerequisite:** the fallback only fires when `handle is None` (no live local ownership), so a respawn by THIS worker would populate the handle and skip the fallback entirely.
**Mitigation:** `subprocess_hang_verdict` re-baselines on a pid change (it keys CPU state by `(session_key, caller, pid)`), so a stale pid that no longer exists returns `hung`/`gone` (correct: the old subprocess is dead) or `unknown` — never a false reprieve.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2071] In-flight-socket-aware post-init hang probe (sub-item 2 code change) — deliberately deferred to documentation only; catching Anthropic-443 post-init hangs faster needs a socket-state-aware probe that risks false-killing legitimate non-443 blocks. Tracked as a revisit note in this same issue's docs.
- Nothing else deferred — sub-items 1 and 3 ship code in this plan, sub-item 2 ships docs.

## Update System

No update system changes required — this is a purely internal worker/health-monitor change. No new deps, config, or migrations (`claude_pid` is an existing AgentSession field; no Popoto schema change).

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. `_tier2_reprieve_signal` and the Fix#3 loop run inside the worker's health/session loops; no MCP surface, `.mcp.json`, or CLI entry point is involved.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-health-monitor.md` — add a subsection documenting (a) the claude_pid probe fallback for orphaned-alive-hung sessions and its ~90s vs 1230s recovery, and (b) the sub-item 2 post-init-hang design tradeoff (gated on `not sdk_ever_output`, `HANG_PROBE_API_PORTS` mitigation, and the revisit rationale for an in-flight-socket-aware probe).

### Inline Documentation
- [ ] Comment the claude_pid fallback branch in `_tier2_reprieve_signal` explaining the orphan case and why it is evidence-safe.
- [ ] Docstring on `_owned_task_hang_check` describing the three-branch contract.

## Success Criteria

- [ ] `_tier2_reprieve_signal` falls back to `entry.claude_pid` when `handle.pid` is None; a hung orphan probes `hung` (~90s) rather than waiting the 1230s grace.
- [ ] `_owned_task_hang_check` extracted as a pure helper; inline Fix#3 block calls it; behavior unchanged.
- [ ] Unit tests cover: claude_pid fallback fires when handle is None; helper confirmed-hang; helper skips probe when `sdk_ever_output=True`; helper no-pid → no hang.
- [ ] `docs/features/agent-session-health-monitor.md` updated for both the fallback and the post-init-hang tradeoff.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms the inline Fix#3 block calls `_owned_task_hang_check`

## Team Orchestration

Small solo change; the dev builds directly and dispatches one code-reviewer.

### Team Members

- **Builder (health-fallback + helper)**
  - Name: hang-followups-builder
  - Role: implement claude_pid fallback + extract `_owned_task_hang_check` + tests
  - Agent Type: builder
  - Domain: async/concurrency (worker health loop, subprocess probe)
  - Resume: true

- **Reviewer**
  - Name: hang-followups-reviewer
  - Role: verify evidence-only safety, single-owner respect, behavioral-equivalence of extraction
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### 1. Wire claude_pid fallback + extract helper + tests
- **Task ID**: build-hang-followups
- **Depends On**: none
- **Validates**: tests/unit/session_runner/test_hang_probe.py, new helper test module
- **Assigned To**: hang-followups-builder
- **Agent Type**: builder
- **Parallel**: false
- Add claude_pid fallback in `_tier2_reprieve_signal` (guarded int coercion, commented).
- Extract `_owned_task_hang_check(entry, pid, *, caller="fix3")` in `agent/agent_session_queue.py`; call it from the inline block at ~L2197.
- Add unit tests for the fallback and the helper's three branches.
- Run the narrow tests; ruff format + check.

### 2. Documentation
- **Task ID**: document-feature
- **Depends On**: build-hang-followups
- **Assigned To**: hang-followups-builder (docs pass) or documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-health-monitor.md` for the fallback and the post-init-hang tradeoff.

### 3. Review
- **Task ID**: review-hang-followups
- **Depends On**: build-hang-followups, document-feature
- **Assigned To**: hang-followups-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify evidence-only safety, #2098 single-owner respect, extraction equivalence, test coverage.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hang-probe tests pass | `scripts/pytest-clean.sh tests/unit/session_runner/test_hang_probe.py -q` | exit code 0 |
| Fix#3 helper is wired | `grep -c "_owned_task_hang_check" agent/agent_session_queue.py` | output > 1 |
| Fallback present | `grep -c "claude_pid" agent/session_health.py` | output > 0 |
| Lint clean | `python -m ruff check agent/session_health.py agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/agent_session_queue.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
