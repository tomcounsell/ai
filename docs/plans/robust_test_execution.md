---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-02-25
tracking:
---

# Robust Test Execution

## Problem

The do-test and do-patch skills have reliability issues that waste significant time during SDLC runs. A real session (PR #171) demonstrated 5 distinct failures:

**Current behavior:**
1. do-test spawns 6 parallel haiku `test-engineer` agents for "run all tests" — haiku agents can't reliably execute bash commands, so they return empty/broken results
2. do-patch calls `/do-test` after a fix, which triggers the full parallel orchestration — massive overhead for a simple verification run (~30s of actual pytest)
3. When parallel agents fail silently, the orchestrator retries the same broken approach 6+ times with no fallback
4. Background Bash commands produce ephemeral output files that vanish before TaskOutput can read them
5. 4+ minutes of churning waiting for agents that are failing silently

**Desired outcome:**
- Test verification after patches runs directly (no subagent dispatch)
- Parallel test dispatch only triggers when it's actually beneficial
- Failed parallel agents trigger a direct-execution fallback
- No more lost test output

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Three skill files to edit. No new infrastructure. Pure process improvement.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **do-patch direct verification**: Replace `/do-test` invocation with inline `pytest` + `ruff` + `black` commands
- **do-test smart dispatch**: Add a `--direct` flag and use it when agent dispatch is wasteful
- **do-test model upgrade**: Specify `model: "sonnet"` for test-engineer subagents (haiku can't bash)
- **do-test fallback path**: When parallel agents fail to produce output, fall back to direct execution

### Flow

**do-patch completes fix** → runs pytest directly → runs lint directly → reports result

**do-test (all)** → discovers test dirs → estimates scale → if small suite or `--direct`: run inline → if large suite: dispatch sonnet agents with 2-min timeout → if agents fail: fallback to direct run

### Technical Approach

1. **do-patch SKILL.md Step 3**: Replace `/do-test` skill invocation with direct bash commands:
   ```
   pytest tests/ -v --tb=short
   ruff check .
   black --check .
   ```
   This eliminates the entire parallel dispatch overhead for patch verification.

2. **do-test SKILL.md "All Tests" section**: Add decision logic before dispatching:
   - Count test files: `find tests/ -name "test_*.py" | wc -l`
   - If < 50 test files OR `--direct` flag: run pytest directly in-process
   - If >= 50: dispatch parallel agents with `model: "sonnet"` specified

3. **do-test SKILL.md parallel dispatch**: Add `model: "sonnet"` to Task calls and add a 2-minute timeout fallback:
   - After dispatching, set a timer
   - If any agent hasn't returned output within 2 minutes, abandon all agents and run tests directly
   - Report which agents failed so the pattern is visible

4. **do-test SKILL.md**: Add `--direct` flag to argument parsing table

## Rabbit Holes

- Don't build a test output caching layer — the problem is agent dispatch, not output storage
- Don't try to fix the TaskOutput ephemeral file issue — that's a Claude Code platform concern; work around it instead
- Don't add retry logic for failed agents — just fall back to direct execution

## Risks

### Risk 1: Direct execution in do-patch misses suite-specific issues
**Impact:** A patch could pass the full suite but mask a test that only fails when run in isolation
**Mitigation:** do-patch runs the full suite directly — same coverage, just no parallelism

### Risk 2: Hardcoded 50-file threshold may not be optimal
**Impact:** Could dispatch agents unnecessarily or miss parallelism benefits
**Mitigation:** 50 is conservative; real benefit of parallelism only shows at scale. Easy to tune later.

## No-Gos (Out of Scope)

- Not fixing the Claude Code TaskOutput/background Bash reliability issue — that's upstream
- Not changing how do-build calls do-test — only changing do-patch's verification path
- Not adding test caching or incremental test runs
- Not restructuring the test-engineer agent definition

## Update System

No update system changes required — this modifies only skill definitions (markdown files), no dependencies or config changes.

## Agent Integration

No agent integration required — these are skill orchestration changes that affect how Claude Code dispatches work internally. No MCP servers, bridge changes, or tool exposure needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/skills-dependency-map.md` to note do-test's `--direct` flag
- [ ] Add entry to `docs/features/README.md` if a new feature doc is created

### Inline Documentation
- [ ] Each skill file change is self-documenting (markdown)

## Success Criteria

- [ ] do-patch runs pytest + lint directly without invoking `/do-test`
- [ ] do-test respects `--direct` flag to skip parallel dispatch
- [ ] do-test parallel agents use `model: "sonnet"` (not haiku)
- [ ] do-test falls back to direct execution when agents fail within 2 minutes
- [ ] End-to-end: `/do-patch` completes test verification in under 60 seconds for this repo's test suite
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skills)**
  - Name: skills-builder
  - Role: Edit do-patch and do-test skill files
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: skills-validator
  - Role: Verify all changes are correct and consistent
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)

## Step by Step Tasks

### 1. Patch do-patch direct verification
- **Task ID**: build-patch-direct
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-patch/SKILL.md` Step 3
- Replace `/do-test` invocation with direct pytest + ruff + black commands
- Keep the same pass/fail logic and retry behavior

### 2. Add --direct flag to do-test
- **Task ID**: build-test-direct
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-test/SKILL.md` argument parsing table to add `--direct`
- Add decision logic in "All Tests" section: count test files, threshold at 50
- If below threshold or `--direct`: run pytest directly, skip agent dispatch

### 3. Upgrade do-test agent model and add fallback
- **Task ID**: build-test-fallback
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-test/SKILL.md` parallel dispatch section
- Add `model: "sonnet"` to all Task() calls for test-engineer agents
- Add 2-minute timeout: if agents haven't returned, abandon and run directly
- Add fallback reporting so failed agents are visible

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-patch-direct, build-test-direct, build-test-fallback
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify do-patch Step 3 no longer references `/do-test`
- Verify do-test argument table includes `--direct`
- Verify do-test parallel dispatch specifies `model: "sonnet"`
- Verify do-test has fallback logic for failed agents
- Verify all success criteria are addressed

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: skills-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/skills-dependency-map.md` with `--direct` flag info
- Add entry to `docs/features/README.md` if needed

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -c "do-test" .claude/skills/do-patch/SKILL.md` - Should return 0 (no do-test invocation)
- `grep -c "\-\-direct" .claude/skills/do-test/SKILL.md` - Should return > 0
- `grep -c "sonnet" .claude/skills/do-test/SKILL.md` - Should return > 0
- `grep -c "fallback" .claude/skills/do-test/SKILL.md` - Should return > 0
