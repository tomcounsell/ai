---
status: Planning
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3316
---

# Widen the pattern-kill validator to cover ui.app, worker, and bridge processes

## Problem

A reviewer agent stopping its own throwaway dashboard with `pkill -f "python -m ui.app"` also killed the production dashboard on port 8500. The pattern matched every `python -m ui.app` process on the machine, not just the one on port 8517. The agent noticed and restarted production, so the outage was short, but nothing warned before the kill landed.

**Current behavior:**
`.claude/hooks/validators/validate_no_broad_process_kill.py` blocks pattern kills only when the pattern names a test runner (`_TEST_RUNNER_PATTERN`, by design per #2562). CLAUDE.md states the broader rule ("never clear processes by pattern; kill by PID"), but the hook enforces the narrow one. Every long-lived service on this machine — `python -m ui.app` (dashboard, 8500), `python -m worker` (session execution engine), `bridge/telegram_bridge.py`, `reflection_worker`, `worker-watchdog` — is reachable by the same unguarded shape.

**Desired outcome:**
The same four kill shapes the validator already covers (`pkill -f`, `kill $(pgrep -f ...)`, `killall`, `pgrep ... | xargs kill`) are blocked when the pattern names any long-lived service, and the block message names the sanctioned stop path for that service (`scripts/valor-service.sh stop`, `worker-stop`, kill by PID for a throwaway instance). The test-runner block keeps working exactly as before.

## Freshness Check

**Baseline commit:** `471a42301`
**Issue filed at:** 2026-09-14T14:55:53Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/validators/validate_no_broad_process_kill.py:36` — `_TEST_RUNNER_PATTERN = r"(?:py\.?test|xdist|pytest-clean)"` still narrow — still holds
- `.claude/hooks/validators/validate_no_broad_process_kill.py:43-59` — four `_BLOCK_PATTERNS` verb shapes — still holds
- `scripts/valor-service.sh:898` — launches `python -m ui.app` — still holds
- `scripts/valor-service.sh:1300,1334` — `stop)` and `worker-stop)` verbs — still holds

**Cited sibling issues/PRs re-checked:**
- #2562 — closed (origin of the validator; its fix stands, this issue widens it)
- #3177 — still open (RSI tracking issue; cited only as surfacing context, unaffected)
- #3215 / PR #3315 — PR merged 2026-09-15 (improvement-controller lane 3; touches controller/dispatch code, not the validator or service scripts)

**Commits on main since issue was filed (touching referenced files):**
- None — `git log --since="2026-09-14T14:55:53Z"` over the validator, its test, and `valor-service.sh` is empty. HEAD moved (`6e22a9ec5` at recon time, `471a42301` at plan time) but none of the movement touches this area.

**Active plans in `docs/plans/` overlapping this area:** none — recent plans cover pytest-clean guards, worktree dispatch, and the RSI controller; none touch the process-kill validator.

**Notes:** The triage section in the issue body proposes inverting the guard (block all pattern kills, allowlist the sanctioned reapers) as the preferred fix with the widen as a stopgap. Recon defers the inversion (see Dropped in the issue's Recon Summary) because it would newly block currently-allowed commands. This plan implements the widen, structured as a data-driven service table so a future inversion reuses it.

## Prior Art

- **Issue #2562**: Full unit-suite runs SIGKILLED by agent-issued machine-wide `kill -9 $(pgrep -f bin/pytest)` — created the validator (commit `e2a623a44`, single creation commit, untouched since). Succeeded for its scope; deliberately narrow, which is exactly the gap this plan closes. No failed attempt; the narrowness was a design decision, not a missed root cause.
- **PR #3208**: Ancestor-safe process lookup closing the BSD pgrep class across every liveness probe (#3187, #3265) — related process-matching hygiene in liveness probes, not in the hook layer. Relevant as a caution: process-name matching has platform edge cases (BSD vs GNU pgrep), so new patterns should stay to plain substring alternatives, not clever regex.
- **Issue #2435** (via test names): a validator written but not dispatched blocks nothing — hence the existing `test_registered_in_the_bash_dispatcher` and dispatcher end-to-end tests, which this plan keeps passing untouched.

No previous fix for this exact problem exists (service-pattern kills were never guarded), so there is no **Why Previous Fixes Failed** section.

## Research

No relevant external findings — proceeding with codebase context and training data. The work is purely internal: one hook predicate, its unit test, and repo-owned service scripts. No external libraries, APIs, or ecosystem patterns are involved, so Phase 0.7 WebSearch is skipped per the skill.

## Data Flow

Single-file synchronous predicate; no multi-component flow to trace. For the record, the enforcement path is:

1. **Entry point**: agent issues a Bash command; `pre_tool_use_bash.py` dispatcher runs before execution
2. **Predicate**: `validate_no_broad_process_kill.find_violation(command)` returns a reason string or `None`
3. **Output**: a reason string blocks the command and shows the agent the sanctioned alternative; `None` lets it through

This plan changes only step 2 (what matches) and the reason text. Dispatcher wiring, hook registration, and the allow/block contract are untouched.

## Appetite

Placeholder.

## Prerequisites

Placeholder.

## Solution

Placeholder.

## Failure Path Test Strategy

Placeholder.

## Test Impact

- [ ] `tests/unit/test_validate_no_broad_process_kill.py` BLOCKED list — UPDATE: add one row per service pattern (`python -m ui.app`, `python -m worker`, `telegram_bridge`, `reflection_worker`, `worker-watchdog`) in each kill-verb shape the validator covers (`pkill -f`, `kill $(pgrep -f ...)`, `killall`, `pgrep ... | xargs kill`), plus a negative row proving a PID kill stays allowed
- [ ] `tests/unit/test_validate_no_broad_process_kill.py` ALLOWED list — UPDATE: add rows proving the sanctioned stop paths are not blocked (`scripts/valor-service.sh stop`, `worker-stop`, `kill -9 <pid>`) and read-only inspection stays allowed
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — no change expected: dispatcher wiring is untouched; the existing end-to-end test keeps passing as a regression guard

No other existing tests affected — the change is confined to one validator predicate and its unit test; no function signatures, imports, or dispatcher behavior change.

## Rabbit Holes

Placeholder.

## Risks

Placeholder.

## Race Conditions

Placeholder.

## No-Gos (Out of Scope)

Placeholder.

## Update System

Placeholder.

## Agent Integration

Placeholder.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pattern-kill-guard.md` describing the validator: which kill shapes are blocked, the service table with per-service sanctioned stop paths, and the kill-by-PID rule for throwaway instances
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on the service table in the validator explaining why each pattern is dangerous (which production process it names)

## Success Criteria

Placeholder.

## Team Orchestration

Placeholder.

## Step by Step Tasks

Placeholder.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Validator unit tests pass | `scripts/pytest-clean.sh tests/unit/test_validate_no_broad_process_kill.py -q` | exit code 0 |
| Incident command now blocked | `python -c "import importlib.util; s=importlib.util.spec_from_file_location('v','.claude/hooks/validators/validate_no_broad_process_kill.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.find_violation('pkill -f \"python -m ui.app\"') is not None"` | exit code 0 |
| PID kill still allowed | `python -c "import importlib.util; s=importlib.util.spec_from_file_location('v','.claude/hooks/validators/validate_no_broad_process_kill.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.find_violation('kill -9 88620') is None"` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Placeholder.
