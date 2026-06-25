---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1784
last_comment_id:
---

# Gate default-tier tool-wedge kill on PTY liveness

## Problem

The per-tool wedge detector in `agent/session_health.py` kills any running
session whose in-flight **default-tier** tool (`Bash`, `Task`, `Skill`,
`WebFetch`) has been running longer than `TOOL_TIMEOUT_DEFAULT_SEC` (300s). The
detector (`_check_tool_timeout`) measures only elapsed time since the tool
started — it cannot distinguish "Bash running a 6-minute test suite" from "Bash
hung forever." SDLC dev sessions legitimately run default-tier tools well past 5
minutes (test suites, installs, builds, long-running `/do-*` **Skill**
invocations), so the kill terminates real work mid-flight.

**Current behavior:**
A default-tier tool that has been live for >300s is recovered (`running ->
pending`, requeued, and eventually finalized `failed` at max attempts) purely on
age — even while it is actively producing PTY output. Verified: session
`tg_cyndra_-1003900483201_163` was finalized `failed` at 2026-06-23 09:13:34 UTC
mid-build with reason `tool-wedge: Bash (default tier) older than 300s`; a re-run
sat productively in a single `Skill` call for 20+ minutes (turn_count 25→37,
tools firing every ~30s) and would have tripped the same kill on slightly
different timing.

**Desired outcome:**
A default-tier tool that is still painting the PTY screen past 300s is never
killed. A genuinely-wedged default-tier tool (no PTY paint, no progress) is still
recovered within a bounded window. Internal (30s) and mcp (120s) tiers are
unchanged.

## Freshness Check

**Baseline commit:** `0c2c2a42` (`0c2c2a4251d4b5ca26fdc35eb430362fc6766834`)
**Issue filed at:** 2026-06-24T14:41:50Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:311` — `TOOL_TIMEOUT_DEFAULT_SEC = int(os.environ.get("TOOL_TIMEOUT_DEFAULT_SEC", 300))` — still holds, line unchanged.
- `agent/session_health.py:322` — `MID_RUN_QUIESCENCE_SECS = 180` — still holds; confirmed NOT consulted by `_check_tool_timeout`.
- `agent/session_health.py:360` — `_check_tool_timeout()` is a pure function flagging wedge on `last_tool_use_at` age vs tier budget only — still holds.
- `agent/session_health.py:~2847-2849` — `_eval_mid_run_pty_stage1(entry, now)` is called immediately before `_check_tool_timeout(entry)` inside `_agent_session_tool_timeout_check`. Confirmed: the quiescence signal (`mid_run_quiescent_since`) is computed on the same entry object in the same tick before the kill decision.
- `models/agent_session.py:388,392,397` — `last_pty_read_loop_at`, `last_pty_activity_at`, `mid_run_quiescent_since` fields all present.

**Cited sibling issues/PRs re-checked:**
- #1724 / PR #1728 — merged; added the mid-run wedge detector and the quiescence fields this fix reuses.
- PR #1781 — still OPEN; fixes the advisory (`never_started`) side, disjoint from the kill side this issue addresses.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=2026-06-24T14:41:50Z -- agent/session_health.py models/agent_session.py` is empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. All issue claims verified against current main.

## Prior Art

- **PR #1728 (issue #1724)**: "recover stalled never_started and mid-run-wedge granite sessions" — added `_eval_mid_run_pty_stage1`, `MID_RUN_QUIESCENCE_SECS`, and the `mid_run_quiescent_since` / `last_pty_activity_at` / `last_pty_read_loop_at` fields. Outcome: shipped, observe-only stage-1. **Directly relevant** — this plan promotes the already-computed stage-1 quiescence signal into a gating conjunct for the default-tier kill.
- **PR #1781**: "suppress never_started false positive for granite PTY sessions" — OPEN. Fixes the *advisory* read-only side. Disjoint from the *kill* side here, but same problem family (PTY-aware liveness for granite sessions).
- **Issue #1270**: introduced the per-tool tiered timeout sub-loop (`_check_tool_timeout`, tier budgets, requeue path) this plan modifies.

## Why Previous Fixes Failed

The 300s default budget (issue #1270) was never *wrong* as a hang ceiling — it
failed because it had **no liveness input**. It treated elapsed-since-start as a
proxy for "stuck," which is false for any legitimately long tool. #1724 built the
liveness signal (`mid_run_quiescent_since`) but wired it only into an observe-only
stage-1 logger, never into the kill decision. So the data existed but the kill
path still flew blind.

**Root cause pattern:** an age-only wedge predicate with the liveness signal
already computed one line above but never consulted.

## Data Flow

1. **Entry point:** `_agent_session_tool_timeout_check` tick (30s sub-loop) iterates every `running` `AgentSession`.
2. **PTY liveness update:** `_eval_mid_run_pty_stage1(entry, now)` reads `last_pty_read_loop_at` / `last_pty_activity_at`, and sets/clears `entry.mid_run_quiescent_since` (the time the screen first went quiescent; cleared when paint resumes). Persisted via `save(update_fields=...)`.
3. **Wedge decision:** `_check_tool_timeout(entry)` returns `(tier, reason)` if `current_tool_name` is set AND `last_tool_use_at` age > tier budget.
4. **New gate (this fix):** for the **default** tier only, the wedge is suppressed unless the PTY has also been quiescent for `>= MID_RUN_QUIESCENCE_SECS`. Internal/mcp tiers keep the age-only predicate.
5. **Recovery:** on a confirmed wedge, the existing race re-read + counter bump + `_apply_recovery_transition` (running -> pending) path runs unchanged.

## Architectural Impact

- **New dependencies:** none — reuses existing fields and constants.
- **Interface changes:** `_check_tool_timeout` gains the liveness gate. To keep it a pure, side-effect-free function and preserve its existing unit contract, the quiescence read is passed in (or read off the entry) rather than triggering new I/O. See Technical Approach for the exact signature decision.
- **Coupling:** slightly increases coupling between the tool-timeout predicate and the PTY-quiescence state — but both already live in the same module and the same tick computes them in sequence.
- **Data ownership:** unchanged. `mid_run_quiescent_since` is still owned/written by `_eval_mid_run_pty_stage1`; `_check_tool_timeout` only reads it.
- **Reversibility:** trivial — set the gate's effective window to 0 (or revert the conjunct) and behavior returns to age-only.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single-file logic change plus unit coverage. Bottleneck is correctness of the gate, not volume.

## Prerequisites

No prerequisites — this work has no external dependencies. The fields and
constants it reads (`mid_run_quiescent_since`, `last_pty_activity_at`,
`MID_RUN_QUIESCENCE_SECS`) already exist on the model and module.

## Solution

### Key Elements

- **PTY-liveness gate on the default tier**: a default-tier tool is only declared wedged when it is BOTH overdue (`age > TOOL_TIMEOUT_DEFAULT_SEC`) AND the PTY has been quiescent for `>= MID_RUN_QUIESCENCE_SECS`. A screen still painting = alive = never killed.
- **Unchanged internal/mcp tiers**: those tools never produce independent PTY paint signals the way a granite dev TUI does and keep the existing age-only predicate. Only the `default` branch gains the conjunct.
- **Bounded-window guarantee for real hangs**: a genuinely wedged default tool (no paint) accumulates quiescence and is still recovered — worst case at `max(TOOL_TIMEOUT_DEFAULT_SEC, MID_RUN_QUIESCENCE_SECS)` plus one tick (both are ≤300s, so the bound is ≈300s + 30s).

### Flow

Tool in flight past 300s → check tier → if internal/mcp: kill on age (unchanged) → if default: is PTY quiescent ≥180s? → **yes** → wedged, recover → **no (still painting)** → alive, skip.

### Technical Approach

- **Where the gate lives:** add the conjunct in `_agent_session_tool_timeout_check` (the consumer at ~line 2849), NOT by adding hidden I/O inside the pure `_check_tool_timeout`. Concretely: keep `_check_tool_timeout` returning the age-based `(tier, reason)` as today, then in the consumer, when `tier == "default"`, evaluate a new helper `_pty_quiescent_long_enough(entry, now)` and `continue` (skip the kill) if it returns False.
- **`_pty_quiescent_long_enough(entry, now) -> bool`:** a new pure helper that returns True when `mid_run_quiescent_since` is set AND `(now - mid_run_quiescent_since) >= MID_RUN_QUIESCENCE_SECS`. Returns False when `mid_run_quiescent_since` is None (screen is painting / freshly active — alive). This mirrors the exact predicate stage-1 already uses at line ~2713, so there is a single definition of "quiescent long enough."
- **Why gate in the consumer, not the predicate:** `_check_tool_timeout` has a documented "pure function, no side effects, safe to call from any tick" contract and an established unit-test surface (10+ tests). Keeping its signature and return contract intact means internal/mcp tier tests and the race re-read logic (which calls `_check_tool_timeout` twice) need no churn. The default-tier liveness gate is layered on top at the one call site that decides recovery.
- **Disable/escape hatch:** because the gate reuses `MID_RUN_QUIESCENCE_SECS`, setting it to 0 already short-circuits stage-1's quiescence accumulation (`mid_run_quiescent_since` is still set on first quiescent tick, but the `> 0` guard governs the CONFIRMED log). The new helper must treat `MID_RUN_QUIESCENCE_SECS <= 0` as "gate disabled → fall back to age-only kill" so operators retain a kill switch identical to today's behavior. This keeps the stopgap (raise `TOOL_TIMEOUT_DEFAULT_SEC`) and the new gate independently tunable.
- **Re-read race parity:** the existing fresh re-read (`AgentSession.get_by_id`) before the transition must also re-evaluate the liveness gate on the fresh row — a tool that resumed painting between the iterator read and the transition must abort the recovery. Apply `_pty_quiescent_long_enough(fresh, now)` in the recheck block alongside the existing `recheck = _check_tool_timeout(fresh)`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_pty_quiescent_long_enough` is a pure read with no `try/except` — it must tolerate `mid_run_quiescent_since=None` and naive datetimes (return False / normalize to UTC) without raising. Test both.
- [ ] The consumer's existing `except Exception` blocks (counter bump, Redis incr) are unchanged by this work — no new swallow points introduced. State: "No new exception handlers in scope; existing ones unchanged."

### Empty/Invalid Input Handling
- [ ] `mid_run_quiescent_since = None` → helper returns False (tool treated as alive, NOT killed). Test.
- [ ] Naive (`tzinfo`-less) `mid_run_quiescent_since` → normalized to UTC, no crash. Test (mirrors `test_check_tool_timeout_handles_naive_datetime`).
- [ ] `MID_RUN_QUIESCENCE_SECS <= 0` → gate disabled, age-only kill restored. Test.

### Error State Rendering
- No user-visible output. The recovery transition emits the existing `(kind=tool_timeout)` log/reason; the reason string is extended to note the quiescence window so logs explain *why* a default-tier kill fired. Verify the reason string includes quiescence context.

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_recovers_wedged_session_default_tier` — UPDATE: the fixture must now also set `mid_run_quiescent_since` old enough to satisfy the gate, else the (correctly) live tool is no longer recovered. Assert recovery still fires when quiescent.
- [ ] `tests/unit/test_session_health_tool_timeout.py::test_check_tool_timeout_fires_over_default_budget` — KEEP as-is: `_check_tool_timeout` itself is unchanged (still age-only). This test continues to assert the predicate's age behavior. Add a sibling test for the new consumer-level gate rather than mutating this one.
- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_internal_tier_classification` / `test_subloop_mcp_tier_classification` — VERIFY-UNCHANGED: internal/mcp tiers must still kill on age with no quiescence requirement. Add an explicit assertion that the gate does NOT apply to these tiers.
- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_aborts_recovery_when_re_read_shows_fresh_state` — VERIFY-UNCHANGED: confirm the new liveness recheck does not break the existing fresh-state abort path.

## Rabbit Holes

- **Rewriting `_eval_mid_run_pty_stage1` to also recover** — out of scope. Stage-1 stays observe-only; this plan only consumes its `mid_run_quiescent_since` output. Do not collapse the two passes.
- **Persisting a real `byte_offset`** (the `total_input_tokens` proxy noted at `models/agent_session.py:399`) — tempting adjacent cleanup, but the gate does not need byte_offset; quiescence-time alone is the conjunct. Leave the proxy as-is.
- **Per-tool-name budgets / tier-splitting `Skill` vs `Bash`** (issue's option 2) — deliberately NOT done. The liveness gate (option 1) fixes the root cause for all default-tier tools at once; tier-splitting adds config surface without addressing the "alive but slow" core problem.
- **Touching the mcp/internal predicates** — those tiers are correct as age-only; do not generalize the gate to them.

## Risks

### Risk 1: A real hang that still produces sporadic PTY paint evades the kill
**Impact:** A tool stuck in a loop that repaints the screen (e.g., a spinner) never accumulates `MID_RUN_QUIESCENCE_SECS` of quiescence and is never recovered.
**Mitigation:** Accepted and bounded. `mid_run_quiescent_since` is set on the first quiescent tick and only cleared on *new* paint; a true hang produces no new paint and trips within ~180s past the 300s budget. A spinner-style live-but-useless loop is a separate, rarer failure mode that the existing main health loop's no-progress / heartbeat checks still cover (turn-count / SDK heartbeat are independent signals). Documented as a known limit.

### Risk 2: `mid_run_quiescent_since` is stale/ABSTAIN due to a dead PTY read loop
**Impact:** If `last_pty_read_loop_at` is stale, stage-1 ABSTAINs and clears `mid_run_quiescent_since` to None — which under the new gate means "not quiescent → don't kill," potentially leaving a session that lost its read loop un-recovered by *this* path.
**Mitigation:** A dead read loop is exactly what the main health loop's `last_pty_read_loop_at` / heartbeat freshness checks already catch and recover via the no_progress path. The tool-timeout path deferring in that case is correct (it lacks a trustworthy liveness signal), not a gap. Add a unit test asserting the gate returns False (defer) when `mid_run_quiescent_since` is None, and document that heartbeat staleness is owned by the main loop.

## Race Conditions

### Race 1: Tool resumes painting between the iterator read and the recovery transition
**Location:** `agent/session_health.py` ~2849-2879 (consumer + fresh re-read block)
**Trigger:** `_eval_mid_run_pty_stage1` marks the entry quiescent; the tool then emits output (clearing `mid_run_quiescent_since`) after the iterator's read but before the transition.
**Data prerequisite:** `mid_run_quiescent_since` must reflect the *current* paint state at transition time.
**State prerequisite:** the session must still be `running` and the tool still in flight.
**Mitigation:** Re-evaluate `_pty_quiescent_long_enough(fresh, now)` on the freshly re-read row in the existing recheck block; abort recovery (continue) if the fresh row is no longer quiescent-long-enough — mirroring the existing `recheck = _check_tool_timeout(fresh)` abort.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1784] Tier-splitting `Skill`/`Task` into their own larger budget (issue option 2) — the liveness gate (option 1) is the chosen root-cause fix; per-tool budgets are explicitly not pursued in this plan and remain tracked on the originating issue should they ever be wanted.
- Nothing else deferred — the liveness gate, its disable switch, the race recheck, and full unit coverage are all in scope.

## Update System

No update system changes required — this is a purely internal logic change in
`agent/session_health.py`. No new dependencies, config files, or env vars (it
reuses the existing `MID_RUN_QUIESCENCE_SECS` and `TOOL_TIMEOUT_DEFAULT_SEC`,
both already propagated via worker env). The worker restart on the next `/update`
picks up the change automatically.

## Agent Integration

No agent integration required — this is a worker-internal health-check change. It
exposes no new tool, MCP server, or bridge entry point. The agent never invokes
`_check_tool_timeout`; it runs inside the worker's health sub-loop.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` doc covering session health / tool-timeout tiers (locate the existing one for issue #1270/#1724; likely under a session-health or granite-pty feature doc) to describe the default-tier PTY-liveness gate and the `MID_RUN_QUIESCENCE_SECS` disable behavior.
- [ ] If no such doc exists, add a short section to the nearest session-lifecycle / bridge-self-healing doc rather than creating a new file.

### Inline Documentation
- [ ] Docstring on `_pty_quiescent_long_enough` stating the predicate, the None-means-alive semantics, and the `<= 0` disable behavior.
- [ ] Comment at the consumer gate explaining why the default tier requires quiescence while internal/mcp do not, referencing issue #1784.

## Success Criteria

- [ ] A default-tier tool emitting PTY output past 300s (`mid_run_quiescent_since` None or recent) is NOT recovered/killed. (unit)
- [ ] A default-tier tool overdue AND quiescent ≥ `MID_RUN_QUIESCENCE_SECS` IS recovered within ≈ one tick. (unit)
- [ ] Internal (30s) and mcp (120s) tiers still kill on age alone, with no quiescence requirement. (unit)
- [ ] `MID_RUN_QUIESCENCE_SECS <= 0` restores age-only default-tier kill (disable switch). (unit)
- [ ] The fresh re-read race path aborts recovery when the tool resumed painting. (unit)
- [ ] No regression to the `tool_timeout` recovery/requeue path or the wedge-field clearing (the #1762 / `15f01deb` family of tests still pass).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (liveness-gate)**
  - Name: gate-builder
  - Role: Implement `_pty_quiescent_long_enough` + wire the default-tier gate and race recheck into `_agent_session_tool_timeout_check`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (gate-coverage)**
  - Name: gate-tester
  - Role: Add/adjust unit tests in `tests/unit/test_session_health_tool_timeout.py` per Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (gate)**
  - Name: gate-validator
  - Role: Verify success criteria, run the tool-timeout test module, confirm no regression in the #1762 family.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the liveness gate
- **Task ID**: build-liveness-gate
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_tool_timeout.py
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add pure helper `_pty_quiescent_long_enough(entry, now) -> bool` near `_check_tool_timeout`: returns True iff `mid_run_quiescent_since` is set, tz-normalized, and `(now - it) >= MID_RUN_QUIESCENCE_SECS`; returns False when `mid_run_quiescent_since` is None; when `MID_RUN_QUIESCENCE_SECS <= 0` return True (gate disabled → age-only kill restored).
- In `_agent_session_tool_timeout_check`, after `tier, reason = check` and when `tier == "default"`, `continue` (skip kill) if `not _pty_quiescent_long_enough(entry, now)`.
- In the fresh re-read block, after `recheck = _check_tool_timeout(fresh)`, also `continue` if `tier == "default"` and `not _pty_quiescent_long_enough(fresh, now)`.
- Extend the default-tier reason string to note quiescence context (e.g. append `+ pty quiescent ≥ {MID_RUN_QUIESCENCE_SECS}s`).
- Add docstring + inline comment referencing issue #1784.

### 2. Add/adjust unit coverage
- **Task ID**: build-tests
- **Depends On**: build-liveness-gate
- **Validates**: tests/unit/test_session_health_tool_timeout.py
- **Assigned To**: gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add: live-but-slow default tool survives (`mid_run_quiescent_since` None/recent → not recovered).
- Add: overdue + quiescent default tool recovered.
- Add: `MID_RUN_QUIESCENCE_SECS <= 0` → age-only kill restored.
- Add: naive-datetime `mid_run_quiescent_since` handled without crash.
- Update `test_subloop_recovers_wedged_session_default_tier` fixture to set quiescence so it still recovers.
- Add explicit assertions that internal/mcp tiers ignore the gate.

### 3. Validate
- **Task ID**: validate-gate
- **Depends On**: build-tests
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_health_tool_timeout.py -q`.
- Confirm all Success Criteria checkboxes.
- Confirm the #1762 / wedge-field-clearing tests still pass.
- Report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-gate
- **Assigned To**: gate-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Update the nearest session-health / tool-timeout feature doc with the default-tier PTY-liveness gate behavior and disable switch.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run lint, format, and the tool-timeout test module.
- Verify all success criteria (including docs).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tool-timeout tests pass | `pytest tests/unit/test_session_health_tool_timeout.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py` | exit code 0 |
| Gate helper exists | `grep -c "_pty_quiescent_long_enough" agent/session_health.py` | output > 1 |
| Default-tier gate wired | `grep -c "_pty_quiescent_long_enough" agent/session_health.py` | output > 2 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_session_health_tool_timeout.py \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. The fix reuses `MID_RUN_QUIESCENCE_SECS` (180s) as the default-tier liveness window. Is reusing the existing #1724 constant acceptable, or should the default-tier gate get its own independently-tunable env var (e.g. `TOOL_TIMEOUT_DEFAULT_QUIESCENCE_SECS`) to decouple it from the stage-1 detector's tuning?
2. Should the documentation land in an existing session-health feature doc, or is there a preferred doc for the tool-timeout tier family that I should target?
