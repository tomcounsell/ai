# Race Condition Analysis in Plans

## Overview

The plan template includes a structured `## Race Conditions` section that prompts planners to systematically analyze timing-dependent bugs, concurrent access patterns, and data/state prerequisites before implementation begins.

## Motivation

Issues #276, #279, and #280 revealed at least 5 race conditions in the bridge/agent pipeline that were never caught during planning. The plan template previously had no section prompting concurrency analysis, so timing-dependent bugs were only discovered in production.

## How It Works

### Plan Template Section

The `## Race Conditions` section appears in `PLAN_TEMPLATE.md` between `## Risks` and `## No-Gos (Out of Scope)`. For each race condition identified, the planner fills out:

- **Location**: File and line range where the race exists
- **Trigger**: What sequence of events causes the race
- **Data prerequisite**: What data must exist/be populated before the dependent operation
- **State prerequisite**: What system state must hold for correctness
- **Mitigation**: How the implementation prevents the race (await, lock, re-read, idempotency, etc.)

If no concurrency concerns exist, the planner states "No race conditions identified" with justification (e.g., "all operations are synchronous and single-threaded").

### Skill Guidance

Phase 1 step 6 in the `/do-plan` skill instructs the planner to perform race condition analysis when the solution involves async operations, shared mutable state, or cross-process data flows.

### Soft Validator

The validator at `.claude/hooks/validators/validate_race_conditions.py` checks whether a plan references async code patterns and, if so, whether it includes a substantive Race Conditions section. It:

- Detects async code by scanning for patterns like `bridge/`, `agent/`, `asyncio`, `async def`, `create_task`, `await`, `threading`, etc.
- Warns (prints to stderr) if the section is missing or incomplete
- Always exits 0 -- it is a soft validator that never blocks plan creation
- Passes silently for plans that do not involve async code

## When to Use

Fill out the Race Conditions section whenever a plan modifies:

- Async code (`async def`, `asyncio`, `create_task`, `await`)
- Bridge or agent pipeline code (`bridge/`, `agent/`)
- Shared mutable state (Redis models, in-memory caches, global variables)
- Cross-process data flows (IPC, message queues, file-based communication)

## Files

| File | Purpose |
|------|---------|
| `.claude/skills/do-plan/PLAN_TEMPLATE.md` | Template with the Race Conditions section |
| `.claude/skills/do-plan/SKILL.md` | Skill with step 6 for race condition analysis |
| `.claude/hooks/validators/validate_race_conditions.py` | Soft validator |
