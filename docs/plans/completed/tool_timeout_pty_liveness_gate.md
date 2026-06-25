---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1784
last_comment_id:
revision_applied: true
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
4. **New gate (this fix):** for the **default** tier only, on a **granite PTY session** the wedge is suppressed unless the PTY has also been quiescent for `>= MID_RUN_QUIESCENCE_SECS`. **Non-PTY (SDK) sessions** — which never set `last_pty_read_loop_at` and so never accumulate `mid_run_quiescent_since` — are NOT gated; they keep the existing age-only default-tier kill. Internal/mcp tiers keep the age-only predicate on every session.
5. **Recovery:** on a confirmed wedge, the existing race re-read + counter bump + `_apply_recovery_transition` (running -> pending) path runs unchanged.

> **Critical:** the same `_agent_session_tool_timeout_check` loop processes BOTH granite PTY sessions and non-granite SDK sessions. `agent/hooks/liveness_writers.py` (the SDK path) writes `current_tool_name` / `last_tool_use_at` but **never** writes `last_pty_read_loop_at` or `mid_run_quiescent_since`. For those rows `_eval_mid_run_pty_stage1` ABSTAINs immediately (`last_pty_read_loop_at is None`, session_health.py:2626-2629), so `mid_run_quiescent_since` stays None forever. The liveness gate MUST therefore distinguish "no PTY at all" (SDK → age-only kill) from "PTY present and currently painting" (granite → alive, defer). Conflating the two would permanently disable the 300s default-tier kill for the entire SDK path — a real regression. The `last_pty_read_loop_at is None → return True` escape in the helper (Technical Approach) is what prevents that.

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

- **PTY-liveness gate on the default tier (granite PTY sessions only)**: on a granite PTY session, a default-tier tool is only declared wedged when it is BOTH overdue (`age > TOOL_TIMEOUT_DEFAULT_SEC`) AND the PTY has been quiescent for `>= MID_RUN_QUIESCENCE_SECS`. A screen still painting = alive = never killed.
- **Non-PTY (SDK) sessions are NOT gated**: SDK / non-granite sessions have no PTY read loop (`last_pty_read_loop_at is None`) and never accumulate `mid_run_quiescent_since`. They keep the existing age-only default-tier kill — the gate must explicitly escape this case (`last_pty_read_loop_at is None → treat as wedge-eligible`) so the 300s ceiling still protects the SDK path.
- **Unchanged internal/mcp tiers**: those tools keep the existing age-only predicate on every session. Only the `default` branch on a granite PTY session gains the conjunct.
- **Bounded-window guarantee for real hangs**: a genuinely wedged default tool on a granite session (no paint) accumulates quiescence and is still recovered — worst case at `max(TOOL_TIMEOUT_DEFAULT_SEC, MID_RUN_QUIESCENCE_SECS)` plus one tick (both are ≤300s, so the bound is ≈300s + 30s). An SDK default tool is recovered at the age ceiling (≈300s + one tick) exactly as today.
- **Decision on tuning constant (resolves Open Question 1):** the gate **reuses the existing `MID_RUN_QUIESCENCE_SECS` (180s)** constant rather than introducing a new `TOOL_TIMEOUT_DEFAULT_QUIESCENCE_SECS`. Rationale: the gate consumes the *same* `mid_run_quiescent_since` field that `_eval_mid_run_pty_stage1` maintains against that exact threshold, so a single source of truth keeps the "quiescent long enough" predicate identical between stage-1's CONFIRMED log and the kill gate. A second env var would let the two drift and silently re-introduce false kills. If future operational data shows they need to decouple, that is a follow-up tuning change, not part of this fix.

### Flow

Tool in flight past 300s → check tier → if internal/mcp: kill on age (unchanged) → if default:
- **no PTY read loop** (`last_pty_read_loop_at is None`, i.e. SDK session) → kill on age (unchanged) →
- **PTY present**: is `mid_run_quiescent_since` set AND ≥180s old? → **yes** → wedged, recover → **no (None / still painting)** → alive, skip.

### Technical Approach

- **Where the gate lives:** add the conjunct in `_agent_session_tool_timeout_check` (the consumer at ~line 2849), NOT by adding hidden I/O inside the pure `_check_tool_timeout`. Concretely: keep `_check_tool_timeout` returning the age-based `(tier, reason)` as today, then in the consumer, when `tier == "default"`, evaluate a new helper `_pty_quiescent_long_enough(entry, now)` and `continue` (skip the kill) if it returns False.
- **`_pty_quiescent_long_enough(entry, now) -> bool`:** a new pure helper whose return value means "**this default-tier tool is wedge-eligible (OK to kill)**." It returns True in any of three cases and False only for a live-painting PTY. **The branch order is load-bearing and MUST be implemented exactly as listed — the first matching branch wins:**

  1. **Kill-switch escape (FIRST, before any field reads):** `if MID_RUN_QUIESCENCE_SECS <= 0: return True`. This restores age-only default-tier kill when an operator zeroes the constant. It must run before reading `last_pty_read_loop_at` / `mid_run_quiescent_since` so the escape hatch is never silently defeated by a None-field short-circuit below it.
  2. **Non-PTY (SDK) escape:** `if last_pty_read_loop_at is None: return True`. A session with no PTY read loop is a non-granite SDK session — it never accumulates `mid_run_quiescent_since`, so the *only* liveness signal available is age. Returning True keeps the existing 300s default-tier kill for the entire SDK path. **Omitting this branch is the critique BLOCKER** — without it, every SDK default-tier tool would fall through to the `mid_run_quiescent_since is None → False` branch and never be killed.
  3. **Painting / freshly-active PTY:** `if mid_run_quiescent_since is None: return False`. The PTY read loop exists (branch 2 didn't fire) but the screen is currently painting (stage-1 cleared `mid_run_quiescent_since`, or it was never quiescent). This is the "alive, defer the kill" case — the whole point of the fix.
  4. **Quiescent long enough:** otherwise tz-normalize `mid_run_quiescent_since` and `return (now - it) >= MID_RUN_QUIESCENCE_SECS`. True once the granite PTY has been quiescent past the window (mirrors the exact predicate stage-1 uses at session_health.py:~2713, so there is a single definition of "quiescent long enough").

  Summary truth table (default tier only):

  | `MID_RUN_QUIESCENCE_SECS` | `last_pty_read_loop_at` | `mid_run_quiescent_since` | quiescent age | returns | meaning |
  |---|---|---|---|---|---|
  | `<= 0` | (any) | (any) | (any) | **True** | kill-switch → age-only kill |
  | `> 0` | None | (any) | (any) | **True** | SDK session → age-only kill |
  | `> 0` | set | None | — | **False** | granite, painting → alive, defer |
  | `> 0` | set | set | `< window` | **False** | granite, quiescent but not long enough → defer |
  | `> 0` | set | set | `>= window` | **True** | granite, quiescent long enough → wedged, recover |
- **Why gate in the consumer, not the predicate:** `_check_tool_timeout` has a documented "pure function, no side effects, safe to call from any tick" contract and an established unit-test surface (10+ tests). Keeping its signature and return contract intact means internal/mcp tier tests and the race re-read logic (which calls `_check_tool_timeout` twice) need no churn. The default-tier liveness gate is layered on top at the one call site that decides recovery.
- **Disable/escape hatch:** `MID_RUN_QUIESCENCE_SECS <= 0` disables the gate entirely (helper branch 1 above), restoring age-only default-tier kill for *every* session — granite and SDK alike — identical to today's behavior. This is the kill switch. It must be the FIRST branch in the helper, before any field reads, so a None-field short-circuit can never silently defeat it. Operators retain two independent levers: raise `TOOL_TIMEOUT_DEFAULT_SEC` (the age ceiling) and/or zero `MID_RUN_QUIESCENCE_SECS` (the liveness gate).
- **Re-read race parity:** the existing fresh re-read (`AgentSession.get_by_id`) before the transition must also re-evaluate the liveness gate on the fresh row — a tool that resumed painting between the iterator read and the transition must abort the recovery. Apply `_pty_quiescent_long_enough(fresh, now)` in the recheck block alongside the existing `recheck = _check_tool_timeout(fresh)`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_pty_quiescent_long_enough` is a pure read with no `try/except` — it must tolerate `mid_run_quiescent_since=None` and naive datetimes (return False / normalize to UTC) without raising. Test both.
- [ ] The consumer's existing `except Exception` blocks (counter bump, Redis incr) are unchanged by this work — no new swallow points introduced. State: "No new exception handlers in scope; existing ones unchanged."

### Empty/Invalid Input Handling
- [ ] `last_pty_read_loop_at = None` (SDK / non-granite session) → helper returns True (age-only kill preserved, tool IS killed at the 300s ceiling). **This is the critique-blocker regression test** — without the escape, SDK default tools would never be killed. Test.
- [ ] `last_pty_read_loop_at` set BUT `mid_run_quiescent_since = None` (granite, painting) → helper returns False (tool treated as alive, NOT killed). Test.
- [ ] Naive (`tzinfo`-less) `mid_run_quiescent_since` → normalized to UTC, no crash. Test (mirrors `test_check_tool_timeout_handles_naive_datetime`).
- [ ] `MID_RUN_QUIESCENCE_SECS <= 0` → gate disabled (FIRST branch), age-only kill restored even when `last_pty_read_loop_at` and `mid_run_quiescent_since` are both set. Test that the kill-switch branch wins ahead of the field reads. Test.

### Error State Rendering
- No user-visible output. The recovery transition emits the existing `(kind=tool_timeout)` log/reason; the reason string is extended to note the quiescence window so logs explain *why* a default-tier kill fired. Verify the reason string includes quiescence context.

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_recovers_wedged_session_default_tier` — UPDATE: the fixture must now set BOTH `last_pty_read_loop_at` (so the row looks like a granite PTY session) AND `mid_run_quiescent_since` old enough to satisfy the gate, else the (correctly) live tool is no longer recovered. Assert recovery still fires when quiescent. **Note:** if the existing fixture leaves `last_pty_read_loop_at` unset, recovery would *also* still fire via the SDK escape branch — so add a sibling test that explicitly sets `last_pty_read_loop_at` and a recent (painting) `mid_run_quiescent_since=None` to prove the granite-painting case is NOT recovered.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD: SDK default-tier session (`last_pty_read_loop_at=None`, `current_tool_name` set, overdue) IS recovered via the age-only escape. This is the regression guard for the critique blocker. REPLACE-or-ADD as a new test case.
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

### Risk 2: `mid_run_quiescent_since` is stale/ABSTAIN due to a dead PTY read loop (granite only)
**Impact:** On a granite session whose `last_pty_read_loop_at` is set but has gone *stale*, stage-1 ABSTAINs and clears `mid_run_quiescent_since` to None — which under the new gate means "painting → don't kill" (branch 3), potentially leaving a granite session that lost its read loop un-recovered by *this* path. (Note: this is distinct from the SDK case, where `last_pty_read_loop_at` is `None` outright and branch 2 keeps the age-only kill.)
**Mitigation:** A dead read loop is exactly what the main health loop's `last_pty_read_loop_at` / heartbeat freshness checks already catch and recover via the no_progress path. The tool-timeout path deferring in that case is correct (it lacks a trustworthy liveness signal for a *granite* session), not a gap. Add a unit test asserting the gate returns False (defer) when `last_pty_read_loop_at` is set but `mid_run_quiescent_since` is None, and document that granite heartbeat staleness is owned by the main loop.

### Risk 3: A non-PTY default-tier tool slips the kill because the gate mistook it for "painting"
**Impact:** If the helper's SDK escape (branch 2) were omitted or ordered after the `mid_run_quiescent_since is None → False` branch, every SDK default tool — which never sets `mid_run_quiescent_since` — would be treated as "painting" and never killed, permanently disabling the 300s ceiling for the entire non-granite path. This is the critique blocker.
**Mitigation:** The helper checks `last_pty_read_loop_at is None → return True` (branch 2) *before* the `mid_run_quiescent_since is None → return False` branch. The branch order in the truth table is mandatory and is covered by the SDK-escape regression test in Test Impact / Success Criteria.

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
- [ ] Docstring on `_pty_quiescent_long_enough` stating the four-branch predicate in order: the `<= 0` kill-switch, the `last_pty_read_loop_at is None` SDK escape (age-only), the `mid_run_quiescent_since is None` painting-means-alive case, and the quiescent-long-enough comparison. Call out that return True = "wedge-eligible / OK to kill."
- [ ] Comment at the consumer gate explaining why the default tier requires quiescence while internal/mcp do not, referencing issue #1784.

## Success Criteria

- [ ] A granite default-tier tool emitting PTY output past 300s (`last_pty_read_loop_at` set, `mid_run_quiescent_since` None or recent) is NOT recovered/killed. (unit)
- [ ] A granite default-tier tool overdue AND quiescent ≥ `MID_RUN_QUIESCENCE_SECS` IS recovered within ≈ one tick. (unit)
- [ ] An **SDK / non-granite default-tier tool** (`last_pty_read_loop_at is None`) overdue past 300s IS recovered via the age-only escape — the gate does NOT disable the kill for the SDK path. (unit, **critique-blocker regression guard**)
- [ ] Internal (30s) and mcp (120s) tiers still kill on age alone, with no quiescence requirement, on both granite and SDK sessions. (unit)
- [ ] `MID_RUN_QUIESCENCE_SECS <= 0` restores age-only default-tier kill for both granite and SDK sessions (disable switch, first branch). (unit)
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
- Add pure helper `_pty_quiescent_long_enough(entry, now) -> bool` near `_check_tool_timeout`, returning True = "wedge-eligible (OK to kill)". Implement the branches **in this exact order** (first match wins):
  1. `if MID_RUN_QUIESCENCE_SECS <= 0: return True` — kill-switch escape, BEFORE any field reads (CONCERN: must be first or the disable hatch is silently defeated).
  2. `if getattr(entry, "last_pty_read_loop_at", None) is None: return True` — non-PTY/SDK escape, age-only kill (BLOCKER fix; without this SDK default tools never die).
  3. `if mid_run_quiescent_since is None: return False` — granite PTY present but painting → alive, defer.
  4. else tz-normalize `mid_run_quiescent_since` and `return (now - it) >= MID_RUN_QUIESCENCE_SECS`.
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
- Add: granite live-but-slow default tool survives (`last_pty_read_loop_at` set, `mid_run_quiescent_since` None/recent → not recovered).
- Add: granite overdue + quiescent default tool recovered.
- Add: **SDK default tool (`last_pty_read_loop_at is None`) overdue → recovered via age-only escape** (critique-blocker regression guard).
- Add: `MID_RUN_QUIESCENCE_SECS <= 0` → age-only kill restored even with `last_pty_read_loop_at` + `mid_run_quiescent_since` both set (kill-switch branch wins first).
- Add: naive-datetime `mid_run_quiescent_since` handled without crash.
- Update `test_subloop_recovers_wedged_session_default_tier` fixture to set `last_pty_read_loop_at` + quiescence so it still recovers via the granite path.
- Add explicit assertions that internal/mcp tiers ignore the gate (on both granite and SDK rows).

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

## Resolved Decisions

These were Open Questions in the prior draft; both are now decided so the plan is build-ready.

1. **Tuning constant — RESOLVED: reuse `MID_RUN_QUIESCENCE_SECS` (180s).** The gate consumes the same `mid_run_quiescent_since` field that `_eval_mid_run_pty_stage1` maintains against this exact threshold; a single source of truth keeps the "quiescent long enough" predicate identical between stage-1's CONFIRMED log and the kill gate, and prevents drift that would silently re-introduce false kills. No new `TOOL_TIMEOUT_DEFAULT_QUIESCENCE_SECS` env var is added. Decoupling, if ever warranted by ops data, is a follow-up tuning change — out of scope here. (See Solution → Key Elements.)
2. **Documentation target — RESOLVED:** the builder updates the nearest existing session-health / tool-timeout feature doc (the doc covering the #1270/#1724 tier + mid-run-wedge work) with the default-tier PTY-liveness gate, the SDK-escape behavior, and the `MID_RUN_QUIESCENCE_SECS` disable switch. Only if no such doc exists does it add a short section to the nearest session-lifecycle / bridge-self-healing doc rather than creating a new file. (See Documentation section.)
