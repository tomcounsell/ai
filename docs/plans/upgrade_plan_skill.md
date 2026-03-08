---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-03-08
tracking: https://github.com/tomcounsell/ai/issues/310
---

# Upgrade Plan Skill with Deep Architectural Analysis

## Problem

The `/do-plan` skill creates plans that focus on implementation steps (what to change, where to change it) but lack deep architectural analysis. Plans describe symptoms but not root causes, propose solutions without reviewing prior art, and plan changes in isolation without tracing data flows end-to-end.

**Current behavior:**
Plans have good structure (Problem, Solution, Risks, Race Conditions, Success Criteria) but miss the investigative work that prevents repeated fixes to the same problem. Issue #309 documents 20+ PRs that each "fixed" stage progress rendering -- each addressing a symptom because no plan ever traced the actual data flow or analyzed why previous attempts failed.

**Desired outcome:**
Plans include Prior Art search results, Data Flow traces, Failure Analysis tables (for recurring problems), and Architectural Impact assessments. The planning workflow actively investigates before proposing solutions, scaled to problem complexity.

## Prior Art

- **Issue #282 / PR #288**: Added `## Race Conditions` section to plan template and SKILL.md. This is the most directly analogous prior change -- same pattern of adding an analytical section to the template plus a corresponding step in the Phase 1 workflow. Successfully merged, no issues reported. The PR also added a soft validator (`validate_race_conditions.py`).
- **Issue #119**: Added code impact finder to blast radius analysis in `/do-plan`. Established the pattern of running investigative tools before writing the plan.
- **Issue #212 / Plan `improve_root_cause_analysis`**: Added Trace & Verify protocol to `/do-patch` and debugging-specialist. Related but operates at bug-fix time, not planning time.
- **Issue #309**: The investigation that motivated this issue. Demonstrated the value of tracing data flows, reviewing prior failed fixes, and doing deep architectural analysis before proposing solutions. This is the exemplar of what plans should contain.
- **45 existing plans in `docs/plans/`**: None include Prior Art, Data Flow, or Failure Analysis sections. All would have benefited from at least the Prior Art search.

## Data Flow

This change modifies prompt/template files, not runtime code. The "data flow" is:

1. User requests a plan (via Telegram or local session)
2. SDLC dispatcher invokes `/do-plan` skill
3. `/do-plan` reads `SKILL.md` for the workflow instructions
4. Agent executes Phase 1 investigation steps (currently: understand, narrow, blast radius, appetite, rough out, race conditions)
5. **[NEW]** Agent executes new investigation steps: prior art search, data flow trace, failure analysis
6. Agent reads `PLAN_TEMPLATE.md` and creates `docs/plans/{slug}.md`
7. **[NEW]** Template includes new sections that agent fills in from investigation
8. Plan is committed and linked to tracking issue

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is purely prompt/template content. No runtime code, no new tools, no API changes.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Updated PLAN_TEMPLATE.md**: Add three new sections (Prior Art, Data Flow, Architectural Impact) plus a conditional Failure Analysis section
- **Updated SKILL.md Phase 1**: Add investigation steps 3a-3c before the existing rough-out step, with instructions to search closed issues, trace data flows, and analyze past failures
- **Complexity scaling guidance**: Instructions in SKILL.md to scale section depth to problem complexity (skip for trivial changes, go deep for recurring bugs)

### Flow

Request arrives -> Understand & narrow (existing) -> Blast radius analysis (existing) -> **Search prior art (NEW)** -> **Trace data flow (NEW)** -> **Analyze failures if recurring (NEW)** -> Set appetite (existing) -> Rough out solution (existing) -> Race condition analysis (existing) -> Write plan with new sections (template) -> Critique & review (existing)

### Technical Approach

- Add 3 new investigation steps to SKILL.md Phase 1, between step 3 (blast radius) and step 4 (set appetite)
- Add 3-4 new sections to PLAN_TEMPLATE.md, placed after the Problem section and before the Appetite section (investigation results should inform appetite)
- Include `gh` CLI commands in SKILL.md for searching closed issues and merged PRs
- Include skip criteria so trivial changes are not burdened with heavy analysis
- Follow the exact pattern established by PR #288 (Race Conditions addition): template section + workflow step + clear guidance on when to skip

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is prompt/template content only

### Empty/Invalid Input Handling
- The new template sections include explicit guidance for the "nothing found" case (e.g., "No prior issues found related to this work")
- Skip criteria prevent empty sections for trivial changes

### Error State Rendering
- No user-visible rendering -- plan documents are markdown files reviewed by humans

## Rabbit Holes

- Building automated tooling to enforce these sections (like a validator) -- defer to a follow-up if needed. The race conditions validator (PR #288) can serve as a model later.
- Requiring ALL sections for every plan regardless of complexity -- this would make simple bug fixes take 10x longer for no benefit
- Trying to auto-populate these sections with AI analysis tools -- the value comes from the agent thinking through these questions, not from automated extraction
- Over-specifying the format of each section -- the race conditions section works well with its structured format, but Prior Art and Data Flow benefit from flexible prose

## Risks

### Risk 1: Prompt context bloat
**Impact:** PLAN_TEMPLATE.md and SKILL.md grow longer, consuming more context window when the agent loads them
**Mitigation:** Keep each new template section concise (5-10 lines of guidance text). The filled-in plan will be longer, but the template itself stays compact. SKILL.md instructions use 2-3 lines per step with example CLI commands.

### Risk 2: Planning takes longer
**Impact:** Each plan requires additional investigation time (searching issues, tracing flows)
**Mitigation:** Complexity scaling guidance explicitly says to skip sections for trivial changes. The issue itself calls this out as a no-go. Expected overhead for medium/large plans: 2-5 minutes of `gh` searches.

## Race Conditions

No race conditions identified -- all operations are synchronous template/prompt editing with no runtime code, shared state, or concurrent access patterns.

## No-Gos (Out of Scope)

- Don't make planning so heavy it blocks small bug fixes -- scale depth to problem complexity
- Don't require all sections for trivial changes -- use judgment (explicitly stated in issue #310)
- Don't remove existing plan sections -- add to them (explicitly stated in issue #310)
- Don't build validators or enforcement hooks -- defer to follow-up
- Don't modify any runtime code, bridge, or agent systems

## Update System

No update system changes required -- this feature modifies skill definition files (`.claude/skills/do-plan/`) that are part of the repository and propagated via normal `git pull`.

## Agent Integration

No agent integration required -- this modifies the `/do-plan` skill template and workflow instructions. The agent already loads these files when the skill is invoked. No new MCP servers, tools, or bridge changes needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/README.md` index table with entry for upgraded plan skill
- [ ] No standalone feature doc needed -- the plan template and SKILL.md are themselves the documentation

### Inline Documentation
- [ ] Comments in PLAN_TEMPLATE.md explaining when each new section should be skipped
- [ ] Comments in SKILL.md explaining the investigation workflow

## Success Criteria

- [ ] PLAN_TEMPLATE.md includes Prior Art, Data Flow, and Architectural Impact sections
- [ ] SKILL.md Phase 1 includes steps for searching closed issues, tracing data flows, and analyzing failures
- [ ] Skip criteria documented for trivial changes (Small appetite, no-code changes)
- [ ] Failure Analysis section is conditional (only for recurring problems)
- [ ] No existing plan sections removed or broken
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (template-updater)**
  - Name: template-updater
  - Role: Update PLAN_TEMPLATE.md and SKILL.md with new analytical sections
  - Agent Type: builder
  - Resume: true

- **Validator (template-validator)**
  - Name: template-validator
  - Role: Verify all files updated correctly and no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update PLAN_TEMPLATE.md with new sections
- **Task ID**: build-template
- **Depends On**: none
- **Assigned To**: template-updater
- **Agent Type**: builder
- **Parallel**: false
- Add `## Prior Art` section after Problem, before Appetite
- Add `## Data Flow` section after Prior Art
- Add `## Why Previous Fixes Failed` section after Data Flow (marked conditional)
- Add `## Architectural Impact` section after Why Previous Fixes Failed
- Each section includes guidance text and skip criteria
- Preserve all existing sections in their current positions (move them down as needed)

### 2. Update SKILL.md Phase 1 with investigation steps
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: template-updater
- **Agent Type**: builder
- **Parallel**: true (with build-template)
- Add step 3a: Prior art search (gh issue list, gh pr list commands)
- Add step 3b: Data flow trace (instructions for multi-component features)
- Add step 3c: Failure analysis (instructions for recurring problems)
- Add complexity scaling guidance (when to skip)
- Renumber existing steps 4-6 accordingly

### 3. Validate template and skill consistency
- **Task ID**: validate-all
- **Depends On**: build-template, build-skill
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify PLAN_TEMPLATE.md has all new sections
- Verify SKILL.md references match template sections
- Verify no existing sections were removed
- Verify skip criteria are documented
- Run ruff format and lint checks on any Python files touched

## Validation Commands

- `grep "## Prior Art" .claude/skills/do-plan/PLAN_TEMPLATE.md` - Prior Art section exists in template
- `grep "## Data Flow" .claude/skills/do-plan/PLAN_TEMPLATE.md` - Data Flow section exists in template
- `grep "## Architectural Impact" .claude/skills/do-plan/PLAN_TEMPLATE.md` - Architectural Impact section exists in template
- `grep "## Why Previous Fixes Failed" .claude/skills/do-plan/PLAN_TEMPLATE.md` - Failure Analysis section exists in template
- `grep -c "Prior art" .claude/skills/do-plan/SKILL.md` - SKILL.md references prior art search
- `grep -c "Data flow" .claude/skills/do-plan/SKILL.md` - SKILL.md references data flow trace
- `grep "gh issue list" .claude/skills/do-plan/SKILL.md` - SKILL.md includes gh CLI commands for investigation
