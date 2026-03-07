---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-07
tracking: https://github.com/tomcounsell/ai/issues/282
---

# Require Race Condition Analysis in Plans

## Problem

The `/do-plan` skill creates structured plan documents but does not prompt the planner to analyze race conditions, concurrency hazards, or data/state prerequisites. Plans that look complete on paper miss timing-dependent bugs that only surface in production.

**Current behavior:**
Issues #276, #279, and #280 all turned out to be race conditions or stale-state bugs in the bridge/agent pipeline. None were caught during planning because the plan template has no section for concurrency analysis. The planner was never asked to think about async timing, shared mutable state, or data prerequisites.

**Desired outcome:**
Plans that modify async code, shared state, or cross-process data flows include a structured Race Conditions section that forces the planner to enumerate timing hazards and specify mitigations before implementation begins.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (code review)

Solo dev work. The template change is small and well-defined. One review round to confirm the template section and skill wording are clear.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Template section**: Add a `## Race Conditions` section to `PLAN_TEMPLATE.md` after `## Risks`
- **Skill guidance**: Update Phase 1 step 5 in `SKILL.md` to prompt race condition analysis
- **Soft validator**: Add a validator that warns (not blocks) if `## Race Conditions` is empty for plans touching async/bridge/agent code

### Technical Approach

#### 1. Template Addition (`PLAN_TEMPLATE.md`)

Insert a new `## Race Conditions` section between `## Risks` and `## No-Gos (Out of Scope)`. The section includes a structured format for each race condition:

```markdown
## Race Conditions

[Enumerate timing-dependent bugs, concurrent access patterns, and data/state prerequisites.
For each hazard identified, fill out the template below. If no concurrency concerns exist,
state "No race conditions identified" with justification (e.g., "all operations are synchronous
and single-threaded").]

### Race N: [Description]
**Location:** [File and line range]
**Trigger:** [What sequence of events causes the race]
**Data prerequisite:** [What data must exist/be populated before the dependent operation]
**State prerequisite:** [What system state must hold for correctness]
**Mitigation:** [How the implementation prevents this -- await, lock, re-read, idempotency, etc.]
```

#### 2. Skill Update (`SKILL.md`)

Add a step 6 after step 5 ("Rough out solution") in Phase 1:

```
6. **Race condition analysis** - If the solution involves async operations, shared mutable state,
   or cross-process data flows, identify timing hazards. For each: specify what data/state must
   be established before dependent operations read it, and how the implementation prevents races.
   Skip if the change is purely synchronous and single-threaded.
```

#### 3. Soft Validator (`.claude/hooks/validators/validate_race_conditions.py`)

A validator script that:
- Checks if the plan modifies files in `bridge/`, `agent/`, or files containing `async`, `asyncio`, `create_task`
- If so, verifies the plan has a `## Race Conditions` section with substantive content
- Exits with code 0 (warning only, does not block) if the section is missing -- prints a warning to stderr
- Exits with code 0 if the plan does not touch async code (not applicable)

The validator is intentionally soft (warn, not block) because:
- Not every plan touching async code has race conditions
- The planner should be prompted to think about it, not punished for deciding there are none
- The explicit "No race conditions identified" statement with justification is a valid response

## Rabbit Holes

- **Retroactively analyzing all existing plans** -- Out of scope. This is forward-looking only.
- **Automated race condition detection** -- Static analysis of async code is a separate, much larger project. The plan template just prompts human analysis.
- **Making the validator a hard blocker** -- Tempting but counterproductive. Many plans legitimately have no race conditions. A hard block would create friction and encourage boilerplate.

## Risks

### Risk 1: Template bloat
**Impact:** Plans become longer and harder to write, discouraging thorough planning
**Mitigation:** The section explicitly allows "No race conditions identified" with justification. Only plans touching async code need detailed analysis.

### Risk 2: Cargo-cult race condition sections
**Impact:** Planners fill in the section with generic boilerplate to satisfy the template without genuine analysis
**Mitigation:** The validator checks for substantive content (not just the header). Code review catches superficial analysis.

## No-Gos (Out of Scope)

- Retroactive analysis of existing plans
- Automated static analysis of async code
- Hard-blocking validation (only soft warnings)
- Changes to any code outside the plan template, skill definition, and validator

## Update System

No update system changes required -- this is a planning process change that only affects skill files and validators within the repository. No dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this changes the plan template and skill guidance that the agent already uses through the existing `/do-plan` skill. No new MCP servers, bridge changes, or tool registrations needed.

## Documentation

- [ ] Create `docs/features/race-condition-analysis.md` describing the new plan section and when to use it
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `PLAN_TEMPLATE.md` contains a `## Race Conditions` section with the structured format
- [ ] `SKILL.md` Phase 1 includes a race condition analysis step
- [ ] Soft validator exists at `.claude/hooks/validators/validate_race_conditions.py`
- [ ] Validator warns when plans touching async code lack a Race Conditions section
- [ ] Validator passes silently for plans that don't touch async code
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (template-and-skill)**
  - Name: template-builder
  - Role: Update PLAN_TEMPLATE.md, SKILL.md, and create the validator script
  - Agent Type: builder
  - Resume: true

- **Validator (completeness)**
  - Name: template-validator
  - Role: Verify all three files are updated correctly and validator works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update Plan Template
- **Task ID**: build-template
- **Depends On**: none
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `## Race Conditions` section to `PLAN_TEMPLATE.md` between `## Risks` and `## No-Gos (Out of Scope)`
- Include structured format with Location, Trigger, Data prerequisite, State prerequisite, Mitigation
- Include guidance for "No race conditions identified" exemption

### 2. Update Skill Definition
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Add step 6 to Phase 1 in `SKILL.md` for race condition analysis
- Reference the new template section

### 3. Create Soft Validator
- **Task ID**: build-validator
- **Depends On**: none
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_race_conditions.py`
- Pattern after `validate_documentation_section.py` structure
- Check if plan touches async code paths
- Warn (not block) if Race Conditions section is missing or empty
- Pass silently for non-async plans

### 4. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: build-template, build-skill, build-validator
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `## Race Conditions` section exists in PLAN_TEMPLATE.md
- Verify step 6 exists in SKILL.md Phase 1
- Run the validator against a plan that touches async code (should warn)
- Run the validator against a plan that doesn't touch async code (should pass silently)
- Verify all success criteria met

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: template-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/race-condition-analysis.md`
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -c '## Race Conditions' .claude/skills/do-plan/PLAN_TEMPLATE.md` - Template has the section (expect 1)
- `grep -c 'Race condition analysis' .claude/skills/do-plan/SKILL.md` - Skill has the step (expect >= 1)
- `test -f .claude/hooks/validators/validate_race_conditions.py` - Validator exists
- `python .claude/hooks/validators/validate_race_conditions.py docs/plans/fix_chat_cross_wire.md` - Validator runs without error on an existing plan

---

## Open Questions

1. **Validator hook registration**: Should the validator be registered in `.claude/settings.local.json` as a Stop hook (like `validate_documentation_section.py`), or is it sufficient to just have the script available for manual use? A Stop hook would prompt the planner during plan creation, but a soft warning might be ignored if it only appears in stderr.

2. **Section ordering**: The issue proposes placing `## Race Conditions` after `## Risks`. An alternative is placing it within `## Risks` as a subsection. The standalone section is more visible, but the subsection approach groups all risk-adjacent analysis together. Which placement is preferred?
