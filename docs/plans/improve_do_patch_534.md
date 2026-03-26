---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/534
last_comment_id:
---

# Improve do-patch skill: require patch before docs stage

## Problem

Issue #534 requested four changes to make the post-review patch stage explicit in the SDLC pipeline. However, PR #550 (merged 2026-03-26 for issue #544) already addressed most of the work. This plan covers only the remaining gaps.

**Current behavior:**
- CLAUDE.md line 166 shows `Plan -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge` -- already includes post-review patch but is stale (missing CRITIQUE stage added by #463)
- SDLC SKILL.md pipeline graph already has `REVIEW(fail|partial) -> PATCH -> TEST -> REVIEW`
- do-patch SKILL.md already has "annotate rather than skip" pattern and handles both test-failure and review-blocker patch types (added by PR #550)
- `config/personas/pm.md` does not exist -- PM decision rules live in `agent/sdk_client.py` (updated by PR #550)

**Desired outcome:**
- CLAUDE.md pipeline description includes CRITIQUE stage and accurately reflects the canonical pipeline graph in `bridge/pipeline_graph.py`
- SDLC SKILL.md dispatch table explicitly labels the post-review patch step with a clarifying comment
- All four acceptance criteria from issue #534 are verified as met

## Prior Art

- **Issue #544 / PR #550**: "PM SDLC decision rules: auto-merge on clean reviews, patch on findings, never silently skip" -- merged 2026-03-26. Updated `do-patch/SKILL.md` with annotate-rather-than-skip, added PM decision rules to `sdk_client.py`. Directly overlaps with issue #534 items 2-3.
- **Issue #399 / PR #412**: "Upgrade SDLC pipeline to directed graph with cycles" -- created `bridge/pipeline_graph.py` with canonical edge definitions including `REVIEW(partial) -> PATCH`.
- **Issue #463**: "Add CRITIQUE stage to SDLC pipeline" -- added CRITIQUE between PLAN and BUILD. CLAUDE.md was not updated to reflect this.

## Data Flow

Not applicable -- this is a documentation/config-only change with no runtime data flow.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **CLAUDE.md pipeline description**: Update line 166 to match the canonical graph: `Plan -> Critique -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge`
- **SDLC SKILL.md dispatch table**: Add a clarifying note to the existing `REVIEW(fail|partial) -> PATCH` row making the post-review-then-docs gate explicit

### Technical Approach

- Read `bridge/pipeline_graph.py` as the single source of truth
- Update CLAUDE.md to match the canonical pipeline
- Add a brief clarifying comment in the SDLC SKILL.md dispatch table row for review-to-patch routing
- Verify all four acceptance criteria from issue #534 are met (most already satisfied by PR #550)

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is purely documentation

### Empty/Invalid Input Handling
- Not applicable -- no code changes

### Error State Rendering
- Not applicable -- no user-visible output changes

## Test Impact

No existing tests affected -- this is a documentation-only chore modifying markdown files. No Python code, no runtime behavior, no test-exercised interfaces change.

## Rabbit Holes

- Rewriting the entire SDLC SKILL.md dispatch table -- it already works, just needs a clarifying note
- Creating `config/personas/pm.md` -- PM decision rules already live in `sdk_client.py` per PR #550, creating a separate file would duplicate them
- Updating `bridge/pipeline_graph.py` -- it is already correct and is the source of truth

## Risks

### Risk 1: Stale documentation diverging from code again
**Impact:** Future SDLC stages added without updating CLAUDE.md
**Mitigation:** CLAUDE.md now explicitly defers to `bridge/pipeline_graph.py` and `.claude/skills/sdlc/SKILL.md` as sources of truth

## Race Conditions

No race conditions identified -- all changes are to static documentation files.

## No-Gos (Out of Scope)

- Creating `config/personas/pm.md` -- PM persona config lives in `sdk_client.py`
- Modifying `bridge/pipeline_graph.py` -- already correct
- Modifying `agent/sdk_client.py` -- already updated by PR #550
- Modifying `do-patch/SKILL.md` beyond a minor comment -- already updated by PR #550

## Update System

No update system changes required -- this is purely internal documentation.

## Agent Integration

No agent integration required -- these are documentation files read by humans and Claude Code, not by the bridge or MCP servers.

## Documentation

- [ ] Verify `docs/features/do-patch-skill.md` (created by PR #550) covers the review-finding patch type
- [ ] No new feature docs needed -- this is a documentation alignment chore

## Success Criteria

- [ ] CLAUDE.md pipeline description includes CRITIQUE stage and matches canonical graph
- [ ] SDLC SKILL.md dispatch table has clarifying note for post-review patch gate
- [ ] do-patch SKILL.md handles both test-failure and review-finding patch types (already done by PR #550 -- verify only)
- [ ] PM persona includes post-review patch in stage orchestration logic (already done in sdk_client.py by PR #550 -- verify only)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (docs-updater)**
  - Name: docs-updater
  - Role: Update CLAUDE.md and SDLC SKILL.md
  - Agent Type: builder
  - Resume: true

- **Validator (docs-validator)**
  - Name: docs-validator
  - Role: Verify all four issue #534 acceptance criteria are met
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update CLAUDE.md pipeline description
- **Task ID**: build-claude-md
- **Depends On**: none
- **Validates**: manual review (no test files -- markdown only)
- **Assigned To**: docs-updater
- **Agent Type**: builder
- **Parallel**: true
- Update line 166 from `Plan -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge` to `Plan -> Critique -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge`

### 2. Add clarifying note to SDLC SKILL.md dispatch table
- **Task ID**: build-sdlc-skill
- **Depends On**: none
- **Validates**: manual review (no test files -- markdown only)
- **Assigned To**: docs-updater
- **Agent Type**: builder
- **Parallel**: true
- In the dispatch table (line 75-77 area), add a comment or note making explicit that REVIEW with findings must route through PATCH before DOCS

### 3. Verify PR #550 completeness
- **Task ID**: validate-pr550
- **Depends On**: build-claude-md, build-sdlc-skill
- **Assigned To**: docs-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm do-patch SKILL.md has "annotate rather than skip" pattern
- Confirm sdk_client.py PM dispatch instructions include post-review patch rules
- Confirm all four issue #534 acceptance criteria are met

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-pr550
- **Assigned To**: docs-validator
- **Agent Type**: validator
- **Parallel**: false
- Run lint checks on any modified files
- Verify success criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CLAUDE.md has Critique | `grep -c 'Critique' CLAUDE.md` | output > 0 |
| Pipeline graph matches | `grep 'REVIEW.*partial.*PATCH' bridge/pipeline_graph.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the scope is clear and most work was already completed by PR #550.
