---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2071
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-16T03:26:33Z
---

# Orphan hang-probe follow-ups (#2069 tail)

## Problem

PR #2070 (issue #2069) widened the never-started D0 grace from 150s to 1230s (~20 min) and added an evidence-based subprocess-hang probe so a genuinely-slow Opus cold start is no longer false-killed. Three hardening/verification follow-ups were deferred; this plan closes them.

**Current behavior (corrected after critique — traced against `main`):**

1. **Alive-hung-orphan recovery is slow — but NOT via the pid-resolution path.** When a worker dies mid-cold-start, its `claude -p` subprocess is orphaned (PPID==1) and may be alive-but-hung (flat CPU, no children, no API socket). The session row stays `running`, `sdk_ever_output=False`, `claude_pid` set. The owning worker's heartbeat loop stopped, so `last_heartbeat_at` ages. Tracing `_has_progress` (`agent/session_health.py:1147`):
   - **If `claude_session_uuid` is set** (SDK authenticated before first output — the common cold-start case): the **#1614 own-progress sticky leg** (`session_health.py:1339-1354`) returns `True` (session "has progress") as long as `last_heartbeat_at` is younger than `NO_OUTPUT_BUDGET_SECONDS` (1800s). Tier-2 is therefore **never reached until ~1800s** after the last heartbeat. The real recovery bottleneck is this 30-min gate, not the pid path.
   - **If `claude_session_uuid` is NOT set**: `_has_progress` returns `False` at ~90s (heartbeat stale past `HEARTBEAT_FRESHNESS_WINDOW`); Tier-2 runs with `handle=None` → `pid=None`; the `unknown`-verdict fall-through `return "alive" if pid is not None else None` returns `None` = **recover immediately (~90s)**. This case is already fast.
   - The original plan's premise (that the orphan waits the 1230s D0 grace because `pid=None` on the pid-resolution path) was **wrong**: the `_tier2_reprieve_signal` `claude_pid` fallback it proposed would have (a) done nothing for the slow uuid-set case (Tier-2 isn't reached) and (b) *regressed* the fast non-uuid case from immediate-recover to a `"alive"` reprieve on the first baseline poll. That fix is dropped.
2. **Post-init hangs wait the full 1800s deadline** — by design, to avoid false-killing a session legitimately blocked on a non-443 endpoint. Deliberate; document, do not change.
3. **The Fix#3 owned-task hang wiring has no direct test.** The inline block at `agent/agent_session_queue.py:2197-2205` was verified by review only.

**Desired outcome:**
1. A genuine alive-hung orphan (owning worker died, subprocess confirmed hung) is released to recovery in ~90s (three flat-CPU probe polls) instead of ~1800s, by adding an **evidence-based subprocess-hang veto to the #1614 own-progress sticky leg**: before honoring a sticky own-progress field, probe `entry.claude_pid`; a `hung`/`gone` verdict releases the session to Tier-2 recovery. Still evidence-only (#1172), still owner-gated (#2098), no change to the load-bearing 1800s heartbeat gate for the non-hung case.
2. The post-init-hang design tradeoff is documented with its revisit rationale.
3. A pure, tested `_owned_task_hang_check` helper replaces the inline Fix#3 hang block, with the pid-resolution covered by a test.

## Freshness Check

**Baseline commit:** `bc1a311b4` (HEAD at plan time)
**Issue filed at:** 2026-07-13T16:27:25Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:1147` `_has_progress` — sub-check A/B + #1614 own-progress leg (L1339-1354) + child check — verified current; this is the corrected locus of the sub-item 1 fix.
- `agent/session_health.py:1339-1354` — #1614 own-progress sticky leg gated on `last_heartbeat_at < NO_OUTPUT_BUDGET_SECONDS` (1800s) — verified.
- `agent/session_health.py:1365` `_tier2_reprieve_signal`, L1447 pid resolution, L1462-1468 unknown fall-through (`return "alive" if pid is not None else None`) — verified; NOT modified by the revised plan.
- `agent/session_health.py:849` `_sweep_dead_worker_sessions` skips alive pids (L896); `:4914` `_reap_orphan_session_processes` hourly OS-process kill — verified (neither is a timely session-recovery path).
- `agent/agent_session_queue.py:2197-2205` inline Fix#3 hang block — verified.
- `agent/session_runner/liveness.py:201-205` `_API_REMOTE_PORTS` env-tunable via `HANG_PROBE_API_PORTS`; `subprocess_hang_verdict` L297 — verified.

**Cited sibling issues/PRs re-checked:**
- #2069 / PR #2070 — merged; the code this plan hardens.
- #2098 (`88575cc33`, merged 2026-07-15) — gates `_agent_session_health_check` actuation to the owning worker process. No root-cause change; the #1614-leg probe runs beneath this gate (in-process, in the owning/live worker's health loop).
- #1614 — the own-progress sticky leg + its 1800s heartbeat gate; the leg the fix augments. #1246 — the wedge the D0 gate bounds.

**Commits on main since issue was filed (touching referenced files):**
- `88575cc33` (#2098) — single-owner actuation (see above); no root-cause change.
- `40b239374`, `d105b33e5`, `4a5a72ff7`, `3bf7e229f` — corrupted-record / stale-index queue hardening; irrelevant to `_has_progress`.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The critique blocker corrected the sub-item 1 diagnosis; the fix locus moved from `_tier2_reprieve_signal` to the `_has_progress` #1614 leg. Baseline regression tests (below) pin current behavior before the change.

## Prior Art

- **PR #2070 (#2069)**: Widened never-started grace + added `subprocess_hang_verdict`. This plan is its explicit deferred tail.
- **#1614**: Gated the own-progress sticky fields (`claude_session_uuid`/`log_path`/`turn_count`) on `last_heartbeat_at < 1800s` to stop zombie sessions being held alive forever. This plan adds an evidence-based hang veto to that leg — a strictly stronger release condition, never a weaker hold.
- **#1172**: Evidence-only kill model — kill on positive evidence, never on output silence. The veto fires only on a positive `hung`/`gone` verdict.
- **#1246 / #1724 / #1905**: The never-started D0 gate and its clock-consistency; unchanged.
- **#1271**: PPID==1 orphan reaper — the hourly OS-process cleanup this plan confirms is not a timely session-recovery path.
- **#2098**: Single-owner health-check actuation — the gate the fix runs beneath.

## Data Flow

1. **Entry point**: worker dies mid-cold-start; its `claude -p` child is reparented to PID 1, alive but hung. Session row: `running`, `sdk_ever_output=False`, `claude_pid` set, `claude_session_uuid` set (authenticated), `last_heartbeat_at` now aging.
2. **Fresh/live worker health loop** (`_agent_session_health_loop` → `_agent_session_health_check`, owner-gated per #2098) evaluates the session and calls `_has_progress`.
3. **`_has_progress`**: sub-check A (no per-turn fields) skip; sub-check B (heartbeat stale >90s) skip; **#1614 own-progress leg**: heartbeat still < 1800s → **today** returns `True` on the sticky `claude_session_uuid` → session held alive until ~1800s.
4. **After fix**: in the #1614 leg, before returning `True`, `subprocess_hang_verdict(entry.claude_pid, session_key, caller="has_progress")` runs. Third flat-CPU poll (~90s) → `hung` → the sticky field is NOT honored → `_has_progress` returns `False`.
5. `_agent_session_health_check` → `_should_kill_no_progress` → `_tier2_reprieve_signal` (handle=None → pid=None → `unknown` → `return None`) → **recover**.
6. **Output**: confirmed-hung orphan recovered in ~90s instead of ~1800s. A healthy live cold start in the same leg probes `progressing` (API socket / CPU / children) → sticky field still honored → stays alive (no regression).

## Architectural Impact

- **New dependencies**: none (`subprocess_hang_verdict` already imported in `session_health.py`).
- **Interface changes**: none externally. `_has_progress` gains an internal probe call in the #1614 leg. New pure helper `_owned_task_hang_check` in `agent/agent_session_queue.py`.
- **Coupling**: `_has_progress` gains a psutil-probe dependency in one narrow leg (already present elsewhere in the module via Tier-2); helper extraction reduces coupling in the Fix#3 loop.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — both changes are localized and revert cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1-2 (the #1614-leg change touches load-bearing convergence code — extra review scrutiny warranted)

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Baseline regression tests (first)**: pin current `_has_progress` behavior for the dead-worker orphan — uuid-set → held True while heartbeat < 1800s; no-uuid → False at stale heartbeat. Prove the diagnosis before changing code.
- **#1614-leg hang veto**: in `_has_progress`, before honoring a sticky own-progress field, probe `entry.claude_pid` via `subprocess_hang_verdict(caller="has_progress")`; a `hung`/`gone` verdict releases the session to Tier-2 recovery. Non-hung (`progressing`/`unknown`) → honor the sticky field exactly as today.
- **`_owned_task_hang_check` helper**: a pure function extracted from the inline Fix#3 block, returning `(hang_detected, hang_gate)`, unit-tested including the `_active_sessions` pid resolution.
- **Post-init-hang design doc**: documented tradeoff + revisit rationale.

### Flow

Worker dies mid-start → orphaned hung `claude -p` (PPID==1) → live worker health tick → `_has_progress` #1614 leg probes `claude_pid` → `hung` at ~90s → returns False → Tier-2 → recover (was: wait ~1800s).

### Technical Approach

- **Sub-item 1** (`agent/session_health.py::_has_progress`, #1614 leg L1339-1354): inside `if _own_progress_fresh:`, before the three sticky-field `return True` checks, compute `_pid = int(entry.claude_pid)` (guarded) and call `verdict, _ = subprocess_hang_verdict(_pid, session_key, caller="has_progress")`. If `verdict == "hung"`, skip the sticky-field returns (fall through → recover). Otherwise honor the sticky fields as today. Keyed `caller="has_progress"` so its flat-count is independent of the Tier-2/Fix#3 probers. Do NOT touch `_tier2_reprieve_signal` (the dropped fallback would regress the fast case per critique).
- **Sub-item 3** (`agent/agent_session_queue.py`): extract
  ```python
  def _owned_task_hang_check(entry, pid, *, caller="fix3") -> tuple[bool, str | None]:
      if derive_sdk_ever_output(entry):
          return (False, None)
      verdict, gate = subprocess_hang_verdict(pid, <session_key>, caller=caller)
      return (verdict == "hung", gate)
  ```
  and call it from the inline block, which keeps its `_active_sessions.get(id).pid` lookup. Add a test that populates a fake `_active_sessions` entry and asserts the resolved pid flows into the decision (covers the pid-resolution, per critique concern), plus the helper's three branches.
- **Sub-item 2**: documentation only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_has_progress` must never raise. The new probe call is wrapped so a malformed/None `claude_pid` (guarded `int()` → None) yields `verdict="unknown"` → sticky field honored (no behavior change, no raise). Test the malformed-claude_pid path.
- [ ] `subprocess_hang_verdict` and `_tier2_reprieve_signal` are already never-raise; `_owned_task_hang_check` inherits that. Test the `sdk_ever_output=True` short-circuit returns `(False, None)` without probing.

### Empty/Invalid Input Handling
- [ ] `_has_progress` with `claude_pid=None` and a fresh heartbeat + sticky uuid → probe `unknown` → returns True (unchanged).
- [ ] `_owned_task_hang_check(entry, None)` → probe `unknown` → `(False, None)`.

### Error State Rendering
- [ ] Not user-visible; recovery is logged via existing `_recovery_reason`/telemetry paths. Reuse `tier2_reprieve_total`/tier1 telemetry; the #1614-leg release logs at INFO via the existing health-check recovery log. No new user-facing surface.

## Test Impact

- [ ] `tests/unit/session_runner/test_hang_probe.py` — UPDATE (add): a case for the `has_progress` caller keying (independent flat-count) if it belongs there; otherwise leave untouched.
- [ ] Health-monitor test module (e.g. `tests/unit/test_session_health*.py` or the closest existing `_has_progress` test home) — ADD: (a) **baseline** tests pinning current behavior (uuid-set orphan held while heartbeat < 1800s; no-uuid orphan False at stale heartbeat); (b) the #1614-leg veto — a `hung` verdict releases (returns False), `progressing`/`unknown` honor the sticky field (returns True); (c) malformed `claude_pid` → unknown → honored.
- [ ] `tests/unit/` (Fix#3 loop) — ADD: `_owned_task_hang_check` helper tests (confirmed-hang, `sdk_ever_output=True` short-circuit, no-pid) plus a pid-resolution test exercising the `_active_sessions` lookup.
- [ ] No existing test asserts the OLD unconditional sticky-field return as a hard contract for a hung subprocess, so no test breaks; the change only adds a release condition. Existing `_has_progress` tests use pids that are absent/live → probe `unknown`/`progressing` → sticky behavior preserved. The builder MUST run the existing health-monitor tests and confirm green (baseline-verify any failure against `main`).

## Rabbit Holes

- **Do NOT lower the 1800s `NO_OUTPUT_BUDGET_SECONDS` heartbeat gate globally** — it is the load-bearing #1614 zombie bound. The fix adds an evidence-based *release* condition, never a shorter hold.
- **PID-recycling paranoia**: do NOT add cmdline-verification to `subprocess_hang_verdict`. Its gone/dead/access-denied handling + the flat-CPU-no-children-no-API confirmation is the accepted safety surface, identical to the shipped Tier-2/Fix#3 probers.
- **In-flight-socket-aware post-init probe** (sub-item 2): out of scope — risks false-killing legitimate non-443 blocks. Document and defer.
- **Refactoring the whole Fix#3 loop**: extract ONLY the hang-check decision; leave the cancel/finalize logic intact.

## Risks

### Risk 1: The #1614-leg probe false-releases a healthy live cold start
**Impact:** A legitimately-slow Opus cold start (authenticated, flat CPU during first-token wait) could be released to recovery if misjudged hung. **Mitigation:** `subprocess_hang_verdict` returns `progressing` on any live child, advancing CPU, or an ESTABLISHED HTTPS socket (the first-token network wait), and only `hung` after `HANG_CONFIRM_SAMPLES` flat polls with none of those. A working cold start trips `progressing`. The `caller="has_progress"` keying keeps its own baseline. A test asserts a cold start with an API socket stays honored (True).

### Risk 2: First-tick baseline behavior
**Impact:** On the first `has_progress` probe poll, `subprocess_hang_verdict` returns `("progressing", "cpu_baseline")` — the sticky field is honored (True). Recovery converges only on the 3rd flat poll (~90s). **Mitigation:** this is the intended ~90s latency (vs ~1800s today); it is a strict improvement and cannot hold a session *longer* than the pre-existing 1800s gate (the gate still bounds the non-hung case). A test asserts tick-1 honored, tick-3 released.

### Risk 3: Helper extraction changes Fix#3 behavior
**Impact:** A subtle behavior change in the owned-task loop kill decision. **Mitigation:** byte-for-byte behavioral extraction (same `derive_sdk_ever_output` gate, same `subprocess_hang_verdict` call, same `verdict == "hung"` test); unit tests pin the three branches and the pid resolution.

## Race Conditions

### Race 1: claude_pid read vs. concurrent respawn in the #1614 leg
**Location:** `agent/session_health.py::_has_progress` (#1614 own-progress leg)
**Trigger:** the owning worker respawns the subprocess (new claude_pid) between the row read and the probe.
**Data prerequisite:** `entry` is a fresh row read within the health tick; `claude_pid` reflects the last spawn.
**State prerequisite:** the probe only affects the decision when the subprocess is confirmed `hung`/`gone`. A live respawn advances CPU/holds an API socket → `progressing` → no false release.
**Mitigation:** `subprocess_hang_verdict` re-baselines on a pid change (state keyed by `(session_key, caller, pid)`), so a stale pid returns `hung`/`gone` (the old subprocess is genuinely dead → correct to release) or re-baselines to `progressing` — never a false hold and never a false-release of a live process.

## No-Gos (Out of Scope)

- [ORDERED] In-flight-socket-aware post-init hang probe (sub-item 2 code change) — deferred to documentation only in this plan; a socket-state-aware probe to catch Anthropic-443 post-init hangs faster is a separate design that must wait until the `HANG_PROBE_API_PORTS` mitigation's real-world signal is reviewed. Blocked on that operational review (a human-gated read of production hang telemetry), not codeable now.
- Nothing else deferred — sub-items 1 and 3 ship code in this plan, sub-item 2 ships docs.

## Update System

No update system changes required — purely internal worker/health-monitor change. No new deps, config, or migrations (`claude_pid` / `claude_session_uuid` are existing AgentSession fields; no Popoto schema change).

## Agent Integration

No agent integration required — bridge/worker-internal change. `_has_progress`, `_tier2_reprieve_signal`, and the Fix#3 loop run inside the worker's health/session loops; no MCP surface, `.mcp.json`, or CLI entry point is involved.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-health-monitor.md` — add a subsection documenting (a) the #1614-leg evidence-based hang veto for orphaned-alive-hung sessions (~90s vs ~1800s recovery, evidence-only, `caller="has_progress"` keying, no change to the 1800s gate for non-hung sessions), and (b) the sub-item 2 post-init-hang design tradeoff (gated on `not sdk_ever_output`, `HANG_PROBE_API_PORTS` mitigation, and the revisit rationale for an in-flight-socket-aware probe).

### Inline Documentation
- [ ] Comment the #1614-leg probe explaining the orphan case, the evidence-only release, and why it never shortens the non-hung hold.
- [ ] Docstring on `_owned_task_hang_check` describing the three-branch contract.

## Success Criteria

- [ ] Baseline regression tests pin current `_has_progress` orphan behavior (uuid-set held <1800s; no-uuid False at stale heartbeat) BEFORE the fix lands.
- [ ] `_has_progress` #1614 leg releases a confirmed-`hung` orphan (returns False → Tier-2 recover) in ~90s; `progressing`/`unknown` honor the sticky field unchanged.
- [ ] A healthy live cold start (API socket) is NOT released (test asserts True).
- [ ] `_tier2_reprieve_signal` is NOT modified (no claude_pid fallback — the critique-proven regression is avoided).
- [ ] `_owned_task_hang_check` extracted as a pure helper; inline Fix#3 block calls it; behavior unchanged; pid-resolution covered by a test.
- [ ] `docs/features/agent-session-health-monitor.md` updated for both the #1614-leg veto and the post-init-hang tradeoff.
- [ ] Reviewer performs an end-to-end sanity check: spawn a session, `kill -9` its worker after `running` + `claude_pid` set but before first tool/turn event, start a fresh worker, and confirm recovery within ~90-120s (vs the ~1800s gate). If infeasible in-environment, document why and rely on the unit coverage.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms the inline Fix#3 block calls `_owned_task_hang_check`

## Team Orchestration

Medium change touching load-bearing health code; the dev builds directly and dispatches one code-reviewer with extra scrutiny on the #1614-leg change.

### Team Members

- **Builder (health-veto + helper)**
  - Name: hang-followups-builder
  - Role: baseline tests + #1614-leg hang veto + extract `_owned_task_hang_check` + tests
  - Agent Type: builder
  - Domain: async/concurrency (worker health loop, subprocess probe)
  - Resume: true

- **Reviewer**
  - Name: hang-followups-reviewer
  - Role: verify evidence-only safety, #1614/#2098 respect, no `_tier2_reprieve_signal` change, extraction equivalence, live-cold-start non-regression, end-to-end sanity
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### 1. Baseline regression tests (prove current behavior)
- **Task ID**: build-baseline-tests
- **Depends On**: none
- **Validates**: health-monitor `_has_progress` orphan behavior
- **Assigned To**: hang-followups-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests pinning: uuid-set orphan held True while `last_heartbeat_at` < 1800s; no-uuid orphan False at stale heartbeat; Tier-2 with pid=None returns None (recover). Confirm green on `main` behavior before the fix.

### 2. #1614-leg hang veto + Fix#3 helper extraction + tests
- **Task ID**: build-hang-followups
- **Depends On**: build-baseline-tests
- **Validates**: new `_has_progress` veto tests, `_owned_task_hang_check` tests, tests/unit/session_runner/test_hang_probe.py
- **Assigned To**: hang-followups-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the evidence-based hang veto to the `_has_progress` #1614 leg (`caller="has_progress"`, guarded claude_pid coercion, commented).
- Extract `_owned_task_hang_check(entry, pid, *, caller="fix3")`; call it from the inline block at ~L2197; add helper + pid-resolution tests.
- Add veto tests (hung releases; progressing/unknown honor; cold-start-with-socket stays True; malformed pid → honored).
- Run the narrow tests; ruff format + check.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-hang-followups
- **Assigned To**: hang-followups-builder (docs pass) or documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-health-monitor.md` for the #1614-leg veto and the post-init-hang tradeoff.

### 4. Review
- **Task ID**: review-hang-followups
- **Depends On**: build-hang-followups, document-feature
- **Assigned To**: hang-followups-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify evidence-only safety, #1614/#2098 respect, no `_tier2_reprieve_signal` change, extraction equivalence, test coverage, end-to-end sanity.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hang-probe tests pass | `scripts/pytest-clean.sh tests/unit/session_runner/test_hang_probe.py -q` | exit code 0 |
| has_progress caller wired | `grep -c 'caller="has_progress"' agent/session_health.py` | output > 0 |
| Fix#3 helper is wired | `grep -c "_owned_task_hang_check" agent/agent_session_queue.py` | output > 1 |
| Tier-2 fallback NOT added (anti-criterion) | `grep -c "claude_pid" agent/session_health.py` inside `_tier2_reprieve_signal` region | (reviewer asserts no claude_pid fallback in `_tier2_reprieve_signal`) |
| Lint clean | `python -m ruff check agent/session_health.py agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/agent_session_queue.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency | Sub-item 1 diagnosis contradicts code: orphan does not wait 1230s on the pid path; real bottleneck is the #1614 1800s own-progress heartbeat gate (uuid-set) or immediate pid=None recovery (no-uuid). | Rewrote Problem/Data-Flow/Approach; moved fix to the `_has_progress` #1614 leg; dropped the `_tier2_reprieve_signal` fallback; added baseline regression tests as Task 1. | Fix vetoes the sticky field on a `hung`/`gone` probe verdict; recovers ~90s vs ~1800s. |
| CONCERN | Risk & Robustness + History & Consistency | Proposed fallback flips `unknown` fall-through from recover→reprieve, regressing the fast no-uuid case first-tick. | Dropped the `_tier2_reprieve_signal` fallback entirely; `_has_progress` fix does not touch that fall-through. | Baseline test pins no-uuid immediate recovery; Success Criteria asserts `_tier2_reprieve_signal` unchanged. |
| CONCERN | Scope & Value | Helper extraction leaves the risky pid-resolution untested. | Added a pid-resolution test exercising the `_active_sessions` lookup. | Test populates a fake `_active_sessions` entry and asserts the resolved pid flows into the decision. |
| CONCERN | Scope & Value | No end-to-end validation of the latency claim. | Added a reviewer end-to-end sanity step (kill -9 worker, observe recovery ~90-120s). | Documented fallback to unit coverage if in-env repro infeasible. |
| NIT | Risk & Robustness | No telemetry to confirm the fix fires. | The #1614-leg release routes through the existing health-check recovery log/telemetry; `caller="has_progress"` distinguishes the probe. | Documented in the feature-doc update. |
| NIT | Scope & Value | Over-specified helper body for a Small plan. | Kept intent; builder chooses exact signature/typing; appetite raised to Medium to reflect the load-bearing #1614 touch. | — |
