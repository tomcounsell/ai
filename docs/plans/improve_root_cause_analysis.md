---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/212
---

# Improve Root Cause Analysis: Trace & Verify Protocol

## Problem

The "5 Whys" root cause analysis used in issues #202 and #207 failed to catch the actual root cause (session ID mismatch) across two separate PRs. The methodology traces backward from symptoms but never verifies forward that proposed fixes actually work through the real system.

**Current behavior:**
- `/do-patch` tells agents to "identify the root cause" but provides no structured methodology
- The debugging-specialist agent has no root cause analysis protocol
- No prompt or guideline requires forward verification, failing tests before fixing, or checking for mocks hiding reality

**Desired outcome:**
- Agent prompts embed the "Trace & Verify" protocol for root cause analysis
- Bug investigation requires concrete data traces, not just narrative reasoning
- Failing tests must be written before fixes are applied
- Mock-hidden integration gaps are explicitly checked for

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is prompt/doc changes only. No runtime code, no new features.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Trace & Verify prompt template**: A reusable protocol embedded in agent prompts that replaces narrative-only root cause analysis with data-driven verification
- **do-patch integration**: Update the patch skill's root cause identification step to use Trace & Verify
- **debugging-specialist update**: Add the protocol as a core methodology alongside existing investigation steps

### Flow

Bug reported -> Agent reads Trace & Verify protocol -> Traces actual data flow at each component boundary -> Writes failing test -> Identifies fix from divergence point -> Verifies fix forward through the trace -> Checks for mocks hiding reality

### Technical Approach

- Add a `## Root Cause Analysis: Trace & Verify` section to `/do-patch` SKILL.md (Step 1 expansion)
- Replace the generic `investigate_issue` methodology in `debugging-specialist.md` with the Trace & Verify steps
- Update `sentry.md` root cause analysis references to point to the protocol
- Create `docs/features/trace-and-verify.md` as the canonical reference

### Files to Modify

1. `.claude/skills/do-patch/SKILL.md` — Add Trace & Verify to Step 1 (root cause identification)
2. `.claude/agents/debugging-specialist.md` — Replace generic investigation with Trace & Verify protocol
3. `.claude/agents/sentry.md` — Update root cause analysis references
4. `docs/features/trace-and-verify.md` — New canonical reference doc

## Rabbit Holes

- Rewriting the entire debugging-specialist agent -- only update the root cause methodology, not the memory/async/performance sections
- Building automated tooling to enforce Trace & Verify -- this is a prompt improvement, not a runtime check
- Trying to retroactively apply Trace & Verify to closed issues -- learn from them, don't reopen them

## Risks

### Risk 1: Prompt bloat
**Impact:** Longer agent prompts consume more context window
**Mitigation:** Keep the protocol concise (< 30 lines in each prompt). The full reference lives in docs/features/, agents get the condensed version.

## No-Gos (Out of Scope)

- No runtime code changes
- No new tools or MCP servers
- No changes to the bridge, SDK client, or job queue
- No automated enforcement (linting, hooks) -- this is a methodology, not a gate

## Update System

No update system changes required -- this is purely prompt/documentation content.

## Agent Integration

No agent integration required -- this modifies existing agent prompts and skill definitions, not runtime code or tool exposure.

## Documentation

- [ ] Create `docs/features/trace-and-verify.md` as the canonical protocol reference
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `/do-patch` SKILL.md includes Trace & Verify protocol in root cause identification step
- [ ] `debugging-specialist.md` references Trace & Verify as primary investigation methodology
- [ ] `sentry.md` root cause analysis references updated
- [ ] `docs/features/trace-and-verify.md` created with full protocol
- [ ] `docs/features/README.md` index updated
- [ ] No existing functionality broken (prompts still parse correctly)

## Team Orchestration

### Team Members

- **Builder (prompt-updater)**
  - Name: prompt-updater
  - Role: Update all agent prompts and skill definitions with Trace & Verify protocol
  - Agent Type: builder
  - Resume: true

- **Validator (prompt-validator)**
  - Name: prompt-validator
  - Role: Verify all files updated correctly and no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create canonical Trace & Verify reference
- **Task ID**: build-reference-doc
- **Depends On**: none
- **Assigned To**: prompt-updater
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/trace-and-verify.md` with the full protocol (from issue #212 body)
- Add entry to `docs/features/README.md` index

### 2. Update do-patch skill
- **Task ID**: build-patch-skill
- **Depends On**: build-reference-doc
- **Assigned To**: prompt-updater
- **Agent Type**: builder
- **Parallel**: false
- Expand Step 1 in `.claude/skills/do-patch/SKILL.md` to include Trace & Verify protocol
- Add reference link to the canonical doc

### 3. Update debugging-specialist agent
- **Task ID**: build-debug-agent
- **Depends On**: build-reference-doc
- **Assigned To**: prompt-updater
- **Agent Type**: builder
- **Parallel**: true (with build-patch-skill)
- Add Trace & Verify as the primary investigation methodology in `.claude/agents/debugging-specialist.md`
- Keep existing memory/async/performance sections untouched

### 4. Update sentry agent
- **Task ID**: build-sentry-agent
- **Depends On**: build-reference-doc
- **Assigned To**: prompt-updater
- **Agent Type**: builder
- **Parallel**: true (with build-patch-skill, build-debug-agent)
- Update root cause analysis references in `.claude/agents/sentry.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-patch-skill, build-debug-agent, build-sentry-agent
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 4 files updated correctly
- Confirm docs/features/README.md index entry exists
- Check no unrelated content was modified

## Validation Commands

- `grep -l "Trace.*Verify" .claude/skills/do-patch/SKILL.md .claude/agents/debugging-specialist.md .claude/agents/sentry.md docs/features/trace-and-verify.md` - All 4 files contain the protocol
- `grep "trace-and-verify" docs/features/README.md` - Index entry exists
- `cat docs/features/trace-and-verify.md | head -5` - Reference doc exists and has content
