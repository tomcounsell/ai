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

Placeholder.

**Current behavior:**
Placeholder.

**Desired outcome:**
Placeholder.

## Freshness Check

Placeholder.

## Prior Art

Placeholder.

## Research

Placeholder.

## Data Flow

Placeholder.

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
