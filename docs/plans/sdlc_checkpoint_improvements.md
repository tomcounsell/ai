---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/203
---

# 5 Whys: SDLC Checkpoint Improvements

## Problem

Issue #177 shipped a complete stage-progress rendering system where every component worked in isolation but the feature never worked in production. The root cause: `tools/session_progress.py` was built but never called by SDLC skills. This slipped through Plan, Build, Test, and Review stages undetected.

A "5 Whys" analysis identified 5 SDLC checkpoints that could have caught at least one oversight. Each needs a modest, targeted improvement — not a rethink.

## Appetite

**Size:** Small (5 one-line additions across 5 files)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

These are documentation-only changes to skill files. No code, no tests to break.

## Prerequisites

None — these are independent documentation improvements.

## Solution

### Key Elements

5 targeted additions to existing SDLC skill docs, each addressing one failure point from the 5 Whys analysis.

### Improvement 1: Plan Success Criteria — Wiring Verification

**File:** `.claude/skills/do-plan/PLAN_TEMPLATE.md`
**What failed:** Plan #177 had success criteria that passed in isolation ("CLI tool works") but didn't verify the tool was actually called by its intended consumer.
**Fix:** Add guidance to the Success Criteria section reminding plan authors to include wiring verification when Agent Integration requires component A to call component B.

Add after the existing success criteria template items (after line 141):

```markdown
- [ ] [If Agent Integration section specifies "X calls Y": grep confirms X references Y]
```

### Improvement 2: Build Definition of Done — Demonstrated Criterion

**File:** `.claude/skills/do-build/SKILL.md`
**What failed:** DoD had 4 criteria (Built, Tested, Quality, Reviewed) — all focused on code artifacts, none on user-visible outcomes.
**Fix:** Add a 5th criterion requiring evidence that the feature produces its intended output.

Add after line 220 ("Reviewed" criterion):

```markdown
- [x] **Demonstrated**: Feature produces intended user-visible output (e.g., rendered message, API response, UI state)
```

### Improvement 3: Test Strategy — Integration Test Guidance

**File:** `.claude/skills/do-test/SKILL.md`
**What failed:** 35+ unit tests passed but no test verified the end-to-end data flow across component boundaries.
**Fix:** Add a note to the test skill reminding it to check for cross-component integration tests when the plan has wiring tasks.

Add to the test dispatching section, as guidance when determining what to test:

```markdown
**Integration test check:** If the plan has an Agent Integration section describing cross-component wiring (tool A feeds component B), verify at least one test exercises the full chain — not just each component in isolation.
```

### Improvement 4: PR Review — Agent Integration Verification

**File:** `.claude/skills/do-pr-review/SKILL.md`
**What failed:** The reviewer verified code correctness and plan alignment but didn't check whether the plan's wiring tasks were completed (Task 7: "add calls to skill docs").
**Fix:** Add one step to Plan Validation (Step 4) to verify Agent Integration wiring.

Add after line 116 ("Verify No-Gos respected"):

```markdown
5. If the plan has an Agent Integration section, verify integration points exist in the codebase (e.g., grep for expected tool calls, imports, or MCP references)
```

### Improvement 5: Post-Ship Audit Methodology — Data Flow Tracing

**File:** `.claude/skills/do-docs-audit/SKILL.md` (or create a brief addendum to the audit methodology)
**What failed:** The summarizer output audit caught 10% compliance but hypothesized the wrong root cause (session parameter is None) instead of the actual cause (history never populated).
**Fix:** Add diagnostic guidance: when output is missing, trace upstream to check whether data is being written, not just whether the renderer works.

This is methodology guidance, not a process gate. Add a note to the docs audit skill or the daydream audit process:

```markdown
**Data flow tracing:** When auditing output compliance, don't just check if the renderer works — trace upstream. Is the data source being populated? Is the tool/function that writes the data actually being called? Grep for expected invocations.
```

## Rabbit Holes

- **Adding automated enforcement** — These are guidance additions, not pre-commit hooks or CI gates. Don't over-engineer.
- **Rewriting the SDLC pipeline** — The pipeline works well. These are 5 one-liners, not a restructure.
- **Adding new test suites** — The improvements are to skill doc guidance. Actual test additions belong in issue #202.
- **Modifying pipeline_state.py or advance_stage()** — Not in scope. This is about human-readable guidance in skill docs.

## Risks

### Risk 1: Guidance gets ignored
**Impact:** Future builders skip the new criteria
**Mitigation:** Keep each addition to 1-2 lines. If it's easy to read, it's easy to follow. The existing DoD criteria are already followed reliably — one more line won't break the pattern.

## No-Gos (Out of Scope)

- Automated CI gates or pre-commit hooks
- Changes to pipeline_state.py, job_queue.py, or any Python code
- New test files (that's issue #202)
- Changes to the summarizer or session_progress tool (that's issue #202)
- Rethinking the SDLC pipeline structure

## Update System

No update system changes required — these are documentation-only changes to skill files that are read in-process.

## Agent Integration

No agent integration required — these are guidance additions to existing skill docs that the agent already reads.

## Documentation

- [ ] Each skill file update IS the documentation improvement
- [ ] Update issue #203 with link to this plan after commit
- [ ] No separate feature doc needed — the improvements are embedded in the skill docs themselves

## Success Criteria

- [ ] `PLAN_TEMPLATE.md` includes wiring verification guidance in Success Criteria
- [ ] `do-build/SKILL.md` DoD has "Demonstrated" as 5th criterion
- [ ] `do-test/SKILL.md` includes integration test check guidance
- [ ] `do-pr-review/SKILL.md` Step 4 includes Agent Integration verification
- [ ] Audit methodology includes data flow tracing guidance
- [ ] All changes are ≤3 lines per file (modest, not disruptive)

## Team Orchestration

### Team Members

- **Builder (docs-updater)**
  - Name: checkpoint-fixer
  - Role: Add one-line improvements to each of 5 skill files
  - Agent Type: builder
  - Resume: true

- **Validator (docs-validator)**
  - Name: checkpoint-validator
  - Role: Verify all 5 files were updated correctly
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation
- `validator` - Read-only verification

## Step by Step Tasks

### 1. Update PLAN_TEMPLATE.md with wiring verification
- **Task ID**: update-plan-template
- **Depends On**: none
- **Assigned To**: checkpoint-fixer
- **Agent Type**: builder
- Add wiring verification line to Success Criteria section

### 2. Update do-build SKILL.md with Demonstrated criterion
- **Task ID**: update-build-dod
- **Depends On**: none
- **Assigned To**: checkpoint-fixer
- **Agent Type**: builder
- Add "Demonstrated" as 5th DoD criterion after "Reviewed"

### 3. Update do-test SKILL.md with integration test guidance
- **Task ID**: update-test-guidance
- **Depends On**: none
- **Assigned To**: checkpoint-fixer
- **Agent Type**: builder
- Add integration test check note to test dispatching section

### 4. Update do-pr-review SKILL.md with Agent Integration check
- **Task ID**: update-review-check
- **Depends On**: none
- **Assigned To**: checkpoint-fixer
- **Agent Type**: builder
- Add step 5 to Plan Validation for Agent Integration verification

### 5. Add data flow tracing guidance to audit methodology
- **Task ID**: update-audit-methodology
- **Depends On**: none
- **Assigned To**: checkpoint-fixer
- **Agent Type**: builder
- Add data flow tracing note to do-docs-audit skill or daydream audit

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: update-plan-template, update-build-dod, update-test-guidance, update-review-check, update-audit-methodology
- **Assigned To**: checkpoint-validator
- **Agent Type**: validator
- Verify each file has the expected addition
- Verify no file has more than 3 lines changed
- Verify all success criteria met

## Validation Commands

- `grep -n "Demonstrated" .claude/skills/do-build/SKILL.md` — Should find the new DoD criterion
- `grep -n "wiring\|grep confirms" .claude/skills/do-plan/PLAN_TEMPLATE.md` — Should find wiring verification
- `grep -n "Integration test check\|cross-component" .claude/skills/do-test/SKILL.md` — Should find integration guidance
- `grep -n "Agent Integration.*verify\|integration points exist" .claude/skills/do-pr-review/SKILL.md` — Should find review check
- `grep -rn "data flow\|trace upstream" .claude/skills/do-docs-audit/` — Should find audit guidance
