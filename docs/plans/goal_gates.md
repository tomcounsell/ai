---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/331
---

# Goal Gates to Prevent Silent Stage Skipping

## Problem

SDLC stages get silently skipped. The Observer Agent (#309) and stage detector improved detection, but there is no **enforcement mechanism** that prevents the pipeline from advancing past a required stage.

**Current behavior:**
1. TEST skipped -- agent runs `/do-build`, gets a passing PR review, and jumps to `/do-docs` without running tests
2. REVIEW skipped -- agent self-approves by moving to docs after tests pass, never invoking `/do-pr-review`
3. DOCS skipped -- agent declares completion after review without creating feature documentation
4. Partial stage -- agent invokes `/do-test` but exits mid-run; stage detector marks TEST as `in_progress` but Observer steers to next stage

**Desired outcome:**
Hard enforcement gates at critical SDLC stages. The `/sdlc` dispatcher and Observer refuse to advance past a gate until the gate condition is met. Gate conditions are deterministic (file exists, PR exists, exit code) -- no LLM judgment involved.

## Prior Art

No closed issues or merged PRs directly address goal gate enforcement. The closest related work:

- **#309 (Observer Agent)**: Replaced fragmented classifier/coach/routing with a single Observer. Improved steering but still relies on LLM judgment for stage progression.
- **#246 (Force SDLC mode)**: Ensures `is_sdlc_job()` returns True from the start. Doesn't enforce gate conditions.
- **#178, #186, #198, #202**: Wired `session_progress` for stage tracking -- but tracking is not enforcement.
- **validate_sdlc_on_stop.py**: Existing hook that checks quality commands (pytest, ruff) were run. This is the closest pattern -- but it only fires at session end, not between stages.

## Data Flow

### Gate check flow (proposed)

1. **Entry point**: `/sdlc` dispatcher decides to advance from stage N to stage N+1
2. **Gate check**: `agent/goal_gates.py::check_gate(stage, slug, working_dir)` runs deterministic checks
3. **GateResult**: Returns `satisfied=True/False` with `evidence` string and `missing` description
4. **Enforcement in /sdlc**: If gate unsatisfied, re-invoke the previous skill (max 2 retries)
5. **Enforcement in Observer**: `read_session` tool result includes gate status for each "completed" stage; Observer steers back to unsatisfied gates
6. **Completion guard**: Before marking session complete, `check_all_gates()` verifies every gate; if any unsatisfied, deliver "incomplete pipeline" message to human

### Integration points

```
/sdlc dispatcher (SKILL.md)
    |-- calls check_gate() before each skill invocation
    |
Observer (bridge/observer.py)
    |-- _handle_read_session() includes gate_status in response
    |
job_queue.py send_to_chat()
    |-- before final delivery, check_all_gates() for SDLC sessions
```

## Architectural Impact

- **New file**: `agent/goal_gates.py` -- pure functions, no side effects, no external dependencies beyond `subprocess` and `pathlib`
- **Modified**: `bridge/observer.py` -- `_handle_read_session()` adds `gate_status` field
- **Modified**: `agent/job_queue.py` -- completion guard in `send_to_chat()` before final delivery
- **Modified**: `.claude/skills/sdlc/SKILL.md` -- gate check instructions before each dispatch
- **Interface changes**: `_handle_read_session()` response gains a `gate_status` dict
- **Coupling**: Low -- `goal_gates.py` is a standalone module with no imports from the rest of the codebase (only stdlib). Observer and job_queue import from it.
- **Reversibility**: Easy -- removing the gate checks reverts to current behavior. Gate module can be deleted without cascading failures.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The core module is a set of pure functions. Integration points are well-defined. The main complexity is ensuring gate checks don't slow down or break the pipeline when conditions are genuinely met.

## Prerequisites

No prerequisites -- this work has no external dependencies. All gate checks use `gh` CLI (already available), `pathlib` (stdlib), and `subprocess` (stdlib).

## Solution

### Key Elements

- **Goal gates module** (`agent/goal_gates.py`): Pure functions that check deterministic conditions for each SDLC stage
- **Dispatcher enforcement**: `/sdlc` SKILL.md instructions that mandate gate checks before advancing
- **Observer integration**: Gate status exposed in `read_session` tool so Observer can steer back to unsatisfied gates
- **Completion guard**: Hard check in `job_queue.py` that prevents marking SDLC sessions complete with unsatisfied gates

### Flow

**Worker finishes stage** --> `/sdlc` checks previous gate --> Gate satisfied? --> Yes: dispatch next skill --> No: re-invoke previous skill (max 2 retries) --> Still unsatisfied: escalate to human

**Observer reads session** --> Includes gate_status per stage --> Stage "completed" but gate unsatisfied? --> Steer back to that stage

**Session completing** --> `check_all_gates()` --> All satisfied: deliver completion --> Any unsatisfied: deliver "incomplete pipeline" warning with evidence

### Technical Approach

- Gate checks are pure functions: `check_gate(stage, slug, working_dir) -> GateResult`
- `GateResult` is a dataclass: `satisfied: bool, evidence: str, missing: str | None`
- Each gate is a simple deterministic check:
  - **PLAN**: `docs/plans/{slug}.md` file exists
  - **BUILD**: `gh pr list --head session/{slug}` returns results
  - **TEST**: Check `data/pipeline/{slug}/state.json` for test stage completion, or check for pytest output markers in session history
  - **REVIEW**: `gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews` returns results, or issue comment starting with "## Review:" exists
  - **DOCS**: Feature doc exists at `docs/features/{slug}.md` OR plan explicitly declares "No documentation changes needed"
- `check_all_gates()` runs all gates and returns a summary
- Gate retry cap: 2 automatic retries per gate before human escalation

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Gate check functions must not raise exceptions -- wrap subprocess calls in try/except and return `GateResult(satisfied=False, evidence="check failed: {error}")` on failure
- [ ] Observer `_handle_read_session()` must handle gate check failures gracefully -- return `gate_status: "error"` rather than crashing

### Empty/Invalid Input Handling
- [ ] Test `check_gate()` with empty slug, None working_dir, nonexistent paths
- [ ] Test `check_all_gates()` with no pipeline state file
- [ ] Verify gate checks fail open (return unsatisfied, not crash) on invalid inputs

### Error State Rendering
- [ ] Completion guard message clearly lists which gates passed and which failed
- [ ] Human-readable evidence for each gate (e.g., "Plan file: docs/plans/my-feature.md exists" vs "Plan file: docs/plans/my-feature.md NOT FOUND")

## Rabbit Holes

- **Graph-based pipeline engine** -- The issue references attractor's graph DSL. We are NOT building a graph engine. Our pipeline is linear; gates are simple checks at transitions.
- **LLM-based gate evaluation** -- Gates must be deterministic. No "ask Claude if tests passed" -- use exit codes and file existence.
- **Partial success semantics** -- Attractor distinguishes SUCCESS vs PARTIAL_SUCCESS. We use pass/fail only. Partial is just "some tests failed" = fail.
- **Automatic fix attempts** -- Gates detect; they don't fix. The retry mechanism is simply re-invoking the same skill, not attempting automated remediation.
- **Retroactive gate enforcement** -- Don't try to check gates for historical sessions or fix past pipeline runs. Gates only apply going forward.

## Risks

### Risk 1: Gate checks slow down pipeline
**Impact:** Each gate check adds latency (especially `gh pr list` and `gh api` calls)
**Mitigation:** Gate checks run only at stage transitions (not continuously). Cache `gh` results within a single gate check call. Each check should complete in <5 seconds.

### Risk 2: False negatives block valid pipelines
**Impact:** Gate reports "unsatisfied" when the condition is actually met (e.g., PR exists but `gh pr list` fails due to rate limiting)
**Mitigation:** On subprocess failure, return `GateResult(satisfied=False, evidence="check failed: {error}")` with clear error message. Human can override. After 2 retries, escalate rather than loop.

### Risk 3: Observer gate integration adds complexity to already complex Observer
**Impact:** Observer system prompt becomes even longer, risking context dilution
**Mitigation:** Keep gate_status as a simple dict in `read_session` response. The Observer's existing logic already checks `has_remaining_stages()` -- gates refine this signal, they don't replace it.

## Race Conditions

No race conditions identified. Gate checks are read-only and stateless -- they query the filesystem and GitHub API at a point in time. The `/sdlc` dispatcher is single-threaded (one skill at a time). The Observer runs synchronously inside `send_to_chat()`. No concurrent writes to gate state.

## No-Gos (Out of Scope)

- Attractor's graph DSL and `goal_gate=true` attribute syntax
- Retry target resolution cascades
- Custom gate definitions per project (gates are hardcoded for the SDLC pipeline)
- Gate configuration files or admin UI
- Performance metrics or dashboards for gate pass/fail rates (future work)
- Typed outcomes from #328 (beneficial but not required -- gates use their own evidence)

## Update System

No update system changes required -- this feature is purely internal to the agent pipeline. The `goal_gates.py` module is a new Python file with no external dependencies beyond stdlib and `gh` CLI (already present on all machines). No config files, environment variables, or migration steps needed.

## Agent Integration

No new MCP server or tool registration needed. The gate checks are invoked:
1. By the `/sdlc` SKILL.md prompt (which instructs the agent to run gate check commands)
2. By `bridge/observer.py` directly importing from `agent/goal_gates.py`
3. By `agent/job_queue.py` directly importing from `agent/goal_gates.py`

The agent does not need to "call" gate checks as tools -- they are infrastructure that runs automatically during pipeline transitions.

## Documentation

- [ ] Create `docs/features/goal-gates.md` describing the gate enforcement system
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings in `agent/goal_gates.py`

## Success Criteria

- [ ] Gate checks defined for PLAN, BUILD, TEST, REVIEW, DOCS stages in `agent/goal_gates.py`
- [ ] `/sdlc` SKILL.md includes gate check instructions before advancing stages
- [ ] Observer `read_session` includes gate_status in its response
- [ ] `check_all_gates()` runs before marking SDLC session complete in `job_queue.py`
- [ ] Gate evidence is deterministic (file exists, PR exists, exit code) -- no LLM judgment
- [ ] Maximum 2 automatic gate-retry attempts before human escalation
- [ ] All existing tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (goal-gates-module)**
  - Name: gates-builder
  - Role: Implement `agent/goal_gates.py` with gate check functions and `GateResult` dataclass
  - Agent Type: builder
  - Resume: true

- **Builder (observer-integration)**
  - Name: observer-builder
  - Role: Wire gate checks into `bridge/observer.py` and `agent/job_queue.py`
  - Agent Type: builder
  - Resume: true

- **Builder (sdlc-skill-update)**
  - Name: skill-builder
  - Role: Update `.claude/skills/sdlc/SKILL.md` with gate check instructions
  - Agent Type: builder
  - Resume: true

- **Validator (gate-checks)**
  - Name: gates-validator
  - Role: Verify gate functions return correct results for all edge cases
  - Agent Type: validator
  - Resume: true

- **Documentarian (feature-docs)**
  - Name: docs-writer
  - Role: Create feature documentation for goal gates
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build Goal Gates Module
- **Task ID**: build-goal-gates
- **Depends On**: none
- **Assigned To**: gates-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/goal_gates.py` with `GateResult` dataclass
- Implement `check_plan_gate(slug, working_dir)` -- checks `docs/plans/{slug}.md` exists
- Implement `check_build_gate(slug, working_dir)` -- checks PR exists for `session/{slug}` branch
- Implement `check_test_gate(slug, working_dir)` -- checks pipeline state for test completion
- Implement `check_review_gate(slug, working_dir)` -- checks PR review exists
- Implement `check_docs_gate(slug, working_dir)` -- checks feature doc exists or explicit skip
- Implement `check_gate(stage, slug, working_dir)` dispatcher
- Implement `check_all_gates(slug, working_dir)` summary function

### 2. Wire Into Observer
- **Task ID**: build-observer-integration
- **Depends On**: build-goal-gates
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `check_all_gates` in `bridge/observer.py`
- Add `gate_status` field to `_handle_read_session()` response dict
- Add gate awareness to Observer system prompt (brief -- one paragraph)

### 3. Wire Into Job Queue Completion Guard
- **Task ID**: build-completion-guard
- **Depends On**: build-goal-gates
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with task 2)
- Import `check_all_gates` in `agent/job_queue.py`
- Before final delivery in `send_to_chat()`, if SDLC session, run `check_all_gates()`
- If any gate unsatisfied, prepend warning message to delivery

### 4. Update SDLC Skill
- **Task ID**: build-sdlc-skill
- **Depends On**: build-goal-gates
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with tasks 2-3)
- Update `.claude/skills/sdlc/SKILL.md` Step 3 table to include gate checks
- Add gate check bash commands before each dispatch
- Document the 2-retry-then-escalate pattern

### 5. Validate Gate Functions
- **Task ID**: validate-gates
- **Depends On**: build-goal-gates, build-observer-integration, build-completion-guard
- **Assigned To**: gates-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each gate function handles missing files, empty inputs, subprocess failures
- Verify `check_all_gates()` returns correct summary
- Run `python -m ruff check agent/goal_gates.py`
- Run `python -m ruff format --check agent/goal_gates.py`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-gates
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/goal-gates.md`
- Add entry to `docs/features/README.md` index table

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-sdlc-skill
- **Assigned To**: gates-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/` to ensure no regressions
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify all success criteria met

## Validation Commands

- `python -c "from agent.goal_gates import check_all_gates; print('import ok')"` - Module imports cleanly
- `python -m ruff check agent/goal_gates.py` - Linting passes
- `python -m ruff format --check agent/goal_gates.py` - Formatting passes
- `pytest tests/ -x` - All tests pass
- `grep -l 'gate_status' bridge/observer.py` - Observer integration wired
- `grep -l 'check_all_gates' agent/job_queue.py` - Completion guard wired

---

## Open Questions

1. **TEST gate evidence**: The issue proposes using `SkillOutcome.status` from `/do-test` (issue #328, Typed outcomes). Since #328 may not be implemented yet, should the TEST gate check `data/pipeline/{slug}/state.json` for test stage completion, or should it check session history for pytest output markers? The plan currently uses pipeline state, which is the simpler approach.

2. **DOCS gate flexibility**: Some plans explicitly state "No documentation changes needed." Should the DOCS gate check the plan document for this phrase, or should there be a separate mechanism (e.g., a flag in pipeline state) to mark docs as intentionally skipped?

3. **Gate check in Observer vs. only in /sdlc**: The issue proposes wiring gates into both the `/sdlc` dispatcher (prompt-level) and the Observer (code-level). The Observer integration adds complexity to an already intricate component. Is prompt-level enforcement in `/sdlc` sufficient, or is the code-level Observer enforcement worth the added complexity?
